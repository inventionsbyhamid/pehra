import brand from "@data/brand";

export default brand;
export const site = `https://${brand.domain}`;
export const mailto = (subject?: string) =>
  `mailto:${brand.contact_email}${subject ? `?subject=${encodeURIComponent(subject)}` : ""}`;
