import test from "node:test";
import assert from "node:assert/strict";
import {
  changeStockQuantity,
  mappingError,
  productPayload,
} from "../src/product-catalog.js";

test("unknown stock stays null while a real zero is preserved and price stays decimal text", () => {
  const form = {
    sku: " ABC ",
    name: " Item ",
    description: "",
    category: "",
    color: "red",
    size: "M",
    price: "10.25",
    currency: "egp",
    stock: "",
    conditions: "",
    url: "",
  };
  assert.equal(productPayload(form).stock_quantity, null);
  assert.equal(productPayload(form).stock_status, "unknown");
  assert.equal(
    productPayload({ ...form, stockStatus: "in_stock" }).stock_status,
    "in_stock",
  );
  assert.equal(
    productPayload({ ...form, stockStatus: "out_of_stock" }).stock_status,
    "out_of_stock",
  );
  const cleared = changeStockQuantity(
    { ...form, stock: "5", stockStatus: "in_stock" },
    "",
  );
  assert.equal(productPayload(cleared).stock_quantity, null);
  assert.equal(productPayload(cleared).stock_status, "unknown");
  assert.equal(productPayload({ ...form, stock: "0" }).stock_quantity, 0);
  assert.equal(productPayload(form).price, "10.25");
  assert.equal(productPayload(form).sku, "ABC");
  assert.equal(productPayload(form).currency, "EGP");
  assert.deepEqual(productPayload(form).options, { color: "red", size: "M" });
  assert.deepEqual(
    productPayload({
      ...form,
      color: "",
      size: "",
      options: { material: "cotton", color: "red" },
    }).options,
    { material: "cotton" },
  );
  assert.deepEqual(
    productPayload({ ...form, color: "", size: "" }).options,
    {},
  );
});
test("mapping requires name, price and explicit currency fallback without duplicate source columns", () => {
  assert.match(mappingError({ price: "Amount" }, "EGP"), /name and price/);
  assert.match(mappingError({ name: "Name", price: "Amount" }, ""), /currency/);
  assert.match(
    mappingError({ name: "Name", price: "Name" }, "EGP"),
    /only.*once/,
  );
  assert.equal(
    mappingError({ name: "Name", price: "Amount", currency: "Currency" }, ""),
    "",
  );
  assert.equal(
    mappingError(
      { name: "Name", price: "Amount", "options.size": "Size" },
      "EGP",
    ),
    "",
  );
});
