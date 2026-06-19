# Going Live — Checklist

Currently running on **Whop sandbox** (test money). This is the path to real payments.

## 0. Confirm sandbox works end-to-end (do first)
- [ ] One test purchase → order appears in admin
- [ ] Mark paid → confirmation email arrives → signed download works
- [ ] Whop webhook auto-marks paid (once registered)

## 1. Whop → production
- [ ] At **whop.com** (NOT sandbox.whop.com), create a production **product** with a one-time plan (any price — the cart total overrides it). Name it honestly, e.g. "Digital Products Order".
- [ ] Developer → API keys → create a **production** API key (`apik_…`)
- [ ] Copy the production **product id** (`prod_…`)
- [ ] Developer → Webhooks → add `https://digital-store-backend-p7cq.onrender.com/api/webhooks/whop`, subscribe to `payment.succeeded`, copy the signing secret
- [ ] Decide tax: let Whop auto-calculate (collects billing address) OR handle yourself

## 2. Env (Render → Environment, and local .env)
- [ ] `WHOP_API_KEY` = production key
- [ ] `WHOP_PRODUCT_ID` = production `prod_…`
- [ ] `WHOP_API_BASE` = `https://api.whop.com`
- [ ] `WHOP_WEBHOOK_SECRET` = production webhook secret
- [ ] `BASE_URL` = `https://digital-store-backend-p7cq.onrender.com`
- [ ] Confirm `DATABASE_URL`, `RESEND_API_KEY`, `R2_*` are all set on Render

## 3. Frontend
- [ ] `checkout.html`: set `const WHOP_ENV = "production";` → push + redeploy

## 4. Email (Resend)
- [ ] Verify your domain (`netgrid.ca` or `peps-checkout.com`) in Resend → add DNS records
- [ ] Set `MAIL_FROM` to an address on the verified domain (e.g. `orders@netgrid.ca`)
- [ ] (Until verified, `onboarding@resend.dev` only emails your own account)

## 5. Currency
- [ ] Sandbox product is USD. For stores in other currencies, either enable local-currency on the Whop product, or create one product per currency and pick by cart currency.

## 6. Per store (×20, one-time each)
- [ ] Paste the checkout-redirect script into the store's `theme.liquid` (uses `{{ shop.name }}` — no per-store config)

## 7. Per product (recurring)
- [ ] Shopify: create product, set **SKU**, mark digital (no shipping), set price
- [ ] Upload file to Supabase (or prepare keys / write access template)
- [ ] Admin → Delivery rule with the **same SKU** + the goods

## 8. Final
- [ ] One real purchase (small amount, real card) end to end
- [ ] Confirm: order paid (via webhook), email delivered, download works

## Compliance note
Confirm with Whop support that using embedded checkout as a gateway for goods
sold/fulfilled on external Shopify stores (one product across many brands) is
permitted — before putting real volume through. Violations risk held funds.
