import brand, { mailto } from "./brand";
import LegalPage from "./LegalPage";
import { freePlan, merchant, refundDays } from "./plans";

export default function Refunds() {
  return (
    <LegalPage title="Refunds and cancellation" intro={<p>Plain rules, the same for every society.</p>}>
      <h2>Monthly plans</h2>
      <p>
        Cancel any time from Billing in the app. Your plan keeps working until the end of the month you paid for, then
        moves to Free. We do not refund part of a month.
      </p>

      <h2>Yearly plans</h2>
      <p>
        You can get a full refund within {refundDays} days of paying for a yearly plan. After {refundDays} days a yearly
        plan is not refunded, and it keeps working until the end of the year you paid for.
      </p>

      <h2>Fewer watched cameras</h2>
      <p>
        You pay for the number of watched cameras you choose. If you remove some, your bill goes down from your next
        renewal; the rest of the current period is not refunded for them.
      </p>

      <h2>How to ask for a refund</h2>
      <p>
        Email <a href={mailto("Refund")}>{brand.contact_email}</a> from your account email with your society's name.{" "}
        {merchant}, our merchant of record, returns the money to the way you paid, including any GST charged. Your bank
        decides how long it takes to show.
      </p>
      <p>You are charged in rupees, and a refund is paid back in rupees too. Dollar amounts on our site are approximate.</p>

      <h2>Your footage after cancelling</h2>
      <p>
        Footage and incident clips stay until the end of the period they were saved under, then they are deleted. From
        then on, Free keeps footage {freePlan.limits.retention_days} days.
      </p>

      <h2>Free plan</h2>
      <p>The Free plan costs nothing, so there is nothing to refund.</p>
    </LegalPage>
  );
}
