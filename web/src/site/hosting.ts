import brand from "./brand";

// Where data is kept. The AWS deploy builds the site with VITE_HOSTING=aws; every other build is the test server, so
// the pages never claim AWS before it runs there.
export const onAws = import.meta.env.VITE_HOSTING === "aws";

export const dataHome = onAws
  ? "On Amazon Web Services in Mumbai, India."
  : `Today ${brand.name} runs on a test server. From its launch on Amazon Web Services in Mumbai, India, data will be kept there.`;

export const aiRoute = onAws
  ? "through Amazon Bedrock on AWS"
  : "through OpenRouter on the test server (through Amazon Bedrock once on AWS)";

export const storageSecurity = onAws
  ? "It is encrypted in storage and in transit."
  : "Traffic to the test server is encrypted, but its files are not yet encrypted at rest.";

export const lockWords = onAws
  ? "On AWS clips are kept in a private S3 bucket until their date, then deleted."
  : "On the test server the app keeps clips until their date.";
