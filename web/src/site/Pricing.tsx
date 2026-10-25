import { Link } from "react-router-dom";
import brand, { mailto } from "./brand";
import PlanGrid, { PlanLinks, PriceExample } from "./PlanGrid";
import { cameraPlan, freePlan, largePlan, merchant } from "./plans";
import SiteLayout from "./SiteLayout";

export default function Pricing() {
  return (
    <SiteLayout>
      <div className="wrap head-block">
        <h1>Pricing per watched camera</h1>
        <p className="lead">
          Pay only for the cameras {brand.name} watches. Start free with{" "}
          {freePlan.limits.watched_cameras} cameras and add more when you are ready.
        </p>
      </div>
      <section className="wrap pricing-plans" aria-label="Plans">
        <PlanGrid level={2} />
        <PriceExample />
        <PlanLinks />
      </section>
      <section className="band band-tint" aria-labelledby="billing-title">
        <div className="wrap split">
          <h2 id="billing-title">Billing, tax and cancelling</h2>
          <dl className="notes">
            <div>
              <dt>Watched cameras</dt>
              <dd>
                A watched camera is a camera connected to {brand.name}. You choose which ones, usually the pool, the
                gates and the parking, and pay for that number. The rest keep recording on your own recorder as they do
                today.
              </dd>
            </div>
            <div>
              <dt>Tax</dt>
              <dd>
                Prices are in rupees and exclude GST. {merchant} is our merchant of record: it runs checkout, adds GST
                where it applies and sends your invoice.
              </dd>
            </div>
            <div>
              <dt>Cancelling</dt>
              <dd>
                Cancel any time from Billing in the app. Your plan keeps working until the end of the period you paid
                for, then moves to Free, which watches {freePlan.limits.watched_cameras} cameras.
              </dd>
            </div>
            <div>
              <dt>Refunds</dt>
              <dd>
                Monthly plans are not refunded for part of a month. Yearly plans can be refunded within 7 days of
                payment. <Link to="/refunds">Read the refund policy</Link>.
              </dd>
            </div>
            <div>
              <dt>Footage</dt>
              <dd>
                {cameraPlan.name} keeps footage {cameraPlan.limits.retention_days} days and Free keeps it{" "}
                {freePlan.limits.retention_days} days, then it is deleted automatically.
              </dd>
            </div>
            <div>
              <dt>{largePlan.name}</dt>
              <dd>
                Malls, schools and townships with {largePlan.limits.min_cameras} or more cameras: email{" "}
                <a href={mailto(`${brand.name} for ${largePlan.name.toLowerCase()}`)}>{brand.contact_email}</a> and we
                will price it for your site.
              </dd>
            </div>
          </dl>
        </div>
      </section>
    </SiteLayout>
  );
}
