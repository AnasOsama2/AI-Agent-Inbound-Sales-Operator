export async function requestAdminReply({
  businessId,
  text,
  api,
  isCurrent,
  onResult,
}) {
  const path = `/api/businesses/${businessId}/admin/messages`;
  const result = await api(path, {
    method: "POST",
    body: JSON.stringify({ text }),
  });
  if (!isCurrent()) return;
  const history = await api(path);
  if (!isCurrent()) return;
  onResult(result, history);
}
