"""Bounded, redacted flow diagnostics; telemetry must never control customer behavior."""

import json
import logging
import os
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache, wraps

log = logging.getLogger("salma.flow")
log.setLevel(logging.INFO)
_context = ContextVar("salma_flow", default=None)
_secrets = re.compile(
    r"lsv2_[A-Za-z0-9_-]+|\b(?:gsk_|sk-)[A-Za-z0-9_-]{12,}|"
    r"Bearer\s+[^\s\"']+|(?:postgres(?:ql)?(?:\+\w+)?|redis)://[^\s\"']+",
    re.I,
)
_email = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_phone = re.compile(r"(?<!\w)(?:\+\d[\d ()-]{8,}\d|0\d[\d ()-]{8,}\d)(?!\w)")
_sensitive = re.compile(
    r"authorization|password|secret|api.?key|(?:^|_)token$|cookie|credential|"
    r"^(?:phone|email|customer_key|external_id|name|contact_details)$",
    re.I,
)


def redact(value, depth=0):
    if depth > 12:
        return "[depth limit]"
    if isinstance(value, dict):
        return {
            str(k)[:160]: "[redacted]" if _sensitive.search(str(k)) else redact(v, depth + 1)
            for k, v in list(value.items())[:100]
        }
    if isinstance(value, (list, tuple)):
        return [redact(item, depth + 1) for item in value[:100]]
    if isinstance(value, str):
        if value.lstrip().startswith(("{", "[")):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, (dict, list)):
                    return json.dumps(redact(parsed, depth + 1), ensure_ascii=False)[:16000]
            except ValueError, RecursionError:
                pass
        return _phone.sub("[phone]", _email.sub("[email]", _secrets.sub("[secret]", value)))[:16000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact(str(value), depth + 1)


def emit(stage, **data):
    context = _context.get()
    if (
        context
        and stage
        in {
            "provider_response",
            "provider_retry",
            "model_usage",
            "tool_rejected",
            "service_description_validation",
            "generation_failed",
            "telemetry_unavailable",
        }
        and len(context.records) < 200
    ):
        context.records.append(
            redact(
                {
                    "stage": stage,
                    "duration_ms": 0,
                    "inputs": {},
                    "outputs": data,
                    "error": data.get("error"),
                }
            )
        )
    payload = {
        "timestamp": time.time(),
        "stage": stage,
        **(context.metadata if context else {}),
        **data,
    }
    log.info(json.dumps(redact(payload), ensure_ascii=False, default=str))


@lru_cache(maxsize=1)
def tracing_client():
    from langsmith import Client

    return Client(
        api_key=os.environ.get("LANGSMITH_API_KEY"),
        api_url=os.environ.get("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com"),
        timeout_ms=2000,
        hide_inputs=redact,
        hide_outputs=redact,
        hide_metadata=redact,
    )


@contextmanager
def open_remote_trace(name, run_type, inputs, metadata, parent=None):
    from langsmith import trace, tracing_context

    with tracing_context(enabled=True, client=tracing_client()):
        with trace(
            name,
            run_type=run_type,
            inputs=redact(inputs or {}),
            metadata=redact(metadata),
            client=tracing_client(),
            project_name=os.environ.get("LANGSMITH_PROJECT", "salma"),
            parent=parent,
            tags=["salma"],
        ) as remote:
            yield remote


@dataclass
class Flow:
    metadata: dict
    records: list = field(default_factory=list)
    remote: object = None
    trace_id: str | None = None
    trace_url: str | None = None
    enabled: bool = False
    trace_parent: dict | None = None


def current_flow():
    return _context.get()


def _enter_remote(name, run_type, inputs, context, parent=None):
    if not context.enabled:
        return None, None
    try:
        manager = open_remote_trace(name, run_type, inputs, context.metadata, parent)
        return manager, manager.__enter__()
    except Exception as exc:
        emit("telemetry_unavailable", error=type(exc).__name__)
        return None, None


def _exit_remote(manager, remote, outputs, error):
    if manager is None:
        return
    try:
        remote.end(outputs=redact(outputs), error=error)
        # Never give the SDK raw application exceptions or their customer-bearing messages.
        manager.__exit__(None, None, None)
    except Exception as exc:
        emit("telemetry_unavailable", error=type(exc).__name__)


@contextmanager
def flow(metadata, *, enabled=None, name="salma.message", parent=None):
    enabled = (
        (
            os.environ.get("LANGSMITH_TRACING", "").lower() == "true"
            and bool(os.environ.get("LANGSMITH_API_KEY"))
        )
        if enabled is None
        else enabled
    )
    context = Flow(metadata=redact(metadata), enabled=enabled)
    token = _context.set(context)
    manager, remote = _enter_remote(name, "chain", {}, context, parent=parent or "ignore")
    context.remote = remote
    if remote:
        try:
            context.trace_id = str(remote.id)
            context.trace_parent = remote.to_headers()
        except Exception as exc:
            emit("telemetry_unavailable", error=type(exc).__name__)
        project_url = os.environ.get("LANGSMITH_PROJECT_URL", "")
        if project_url.startswith("https://smith.langchain.com/"):
            context.trace_url = project_url + "?peek=" + context.trace_id
    started, error = time.monotonic(), None
    emit("flow_started", trace_id=context.trace_id, trace_url=context.trace_url)
    try:
        yield context
    except BaseException as exc:
        error = type(exc).__name__
        raise
    finally:
        outputs = {"stage_count": len(context.records), "error": error}
        emit("flow_finished", duration_ms=round((time.monotonic() - started) * 1000), **outputs)
        _exit_remote(manager, remote, outputs, error)
        _context.reset(token)


@contextmanager
def phase(name, *, inputs=None, run_type="chain"):
    context = _context.get()
    outputs, error, started = {}, None, time.monotonic()
    manager, remote = (None, None)
    if context:
        # The SDK supplies the active nested parent; the message flow is the fallback.
        from langsmith.run_helpers import get_current_run_tree

        manager, remote = _enter_remote(
            name, run_type, inputs, context, parent=get_current_run_tree() or context.remote
        )
    emit(name + ".started", inputs=inputs or {})
    try:
        yield outputs
    except BaseException as exc:
        error = type(exc).__name__
        raise
    finally:
        record = redact(
            {
                "stage": name,
                "duration_ms": round((time.monotonic() - started) * 1000),
                "inputs": inputs or {},
                "outputs": outputs,
                "error": error,
            }
        )
        if context and len(context.records) < 200:
            context.records.append(record)
        emit(name + ".finished", **{k: v for k, v in record.items() if k != "stage"})
        _exit_remote(manager, remote, outputs, error)


def diagnostics():
    context = _context.get()
    if not context:
        return {}
    return {
        "stages": list(context.records),
        "trace_id": context.trace_id,
        "trace_parent": context.trace_parent,
        "trace_url": context.trace_url,
        "project": os.environ.get("LANGSMITH_PROJECT", "salma") if context.enabled else None,
    }


def observed(name, *, run_type="chain"):
    """Instrument methods without serializing their self/configuration objects."""

    def decorate(function):
        @wraps(function)
        def wrapped(self, *args, **kwargs):
            with phase(name, inputs={"args": args, "kwargs": kwargs}, run_type=run_type) as out:
                result = function(self, *args, **kwargs)
                if isinstance(result, dict):
                    out.update(
                        {
                            k: v.model_dump() if hasattr(v, "model_dump") else v
                            for k, v in result.items()
                        }
                    )
                    if run_type == "llm" and getattr(self, "usage", None):
                        usage = self.usage[-1]
                        out["usage_metadata"] = {
                            "input_tokens": usage.get("prompt_tokens", 0),
                            "output_tokens": usage.get("completion_tokens", 0),
                            "total_tokens": usage.get("total_tokens", 0),
                        }
                        out["model"] = self.config.groq_model
                else:
                    out["result"] = result
                return result

        return wrapped

    return decorate
