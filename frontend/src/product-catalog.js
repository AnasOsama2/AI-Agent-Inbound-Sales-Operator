export const PRODUCT_FIELDS = [
  "sku",
  "name",
  "description",
  "category",
  "options",
  "options.color",
  "options.size",
  "price",
  "currency",
  "stock_quantity",
  "stock_status",
  "conditions",
  "url",
];
export const IMPORT_PROCESSING = new Set([
  "pending",
  "processing",
  "parsing",
  "validating",
  "pending_validation",
  "applying",
]);
export function changeStockQuantity(form, value) {
  return {
    ...form,
    stock: value,
    stockStatus: value === "" ? "unknown" : form.stockStatus || "unknown",
  };
}
export function productPayload(form) {
  return {
    sku: form.sku.trim(),
    name: form.name.trim(),
    description: form.description.trim(),
    category: form.category.trim(),
    options: Object.fromEntries(
      Object.entries({
        ...(form.options || {}),
        color: form.color.trim(),
        size: form.size.trim(),
      }).filter(([, value]) => value !== "" && value != null),
    ),
    price: form.price,
    currency: form.currency.trim().toUpperCase(),
    stock_quantity: form.stock === "" ? null : Number(form.stock),
    stock_status: form.stockStatus || "unknown",
    conditions: form.conditions.trim(),
    url: form.url.trim(),
  };
}
export function mappingError(mapping, currency) {
  const selected = Object.values(mapping).filter(Boolean);
  if (!mapping.name || !mapping.price)
    return "Map a product name and price column before previewing.";
  if (new Set(selected).size !== selected.length)
    return "Each source column can only be mapped once.";
  if (!mapping.currency && !/^[A-Z]{3}$/.test(currency))
    return "Choose a currency column or enter a three-letter default currency.";
  return "";
}
