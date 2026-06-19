# Dispatch — Digital Goods Checkout

A **drop-in checkout + delivery + admin** layer for a **Shopify** store selling **digital goods** (license keys, file downloads, access grants). Shopify is the storefront; it redirects the buyer here with their cart, this app takes payment and delivers the goods to the buyer's inbox instantly. No storefront, no shipping, no physical fulfillment.

```
Shopify storefront ──redirect with signed cart──▶ this checkout ──pay──▶ deliver goods + email ──▶ admin manages orders
```

Stack: **FastAPI + SQLAlchemy** (SQLite default, swap to MariaDB/Postgres via one env var) and two zero-build static pages (checkout + admin).

---

## Quick start

```bash
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1     # Windows PowerShell  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env         # macOS/Linux: cp .env.example .env  — edit secrets for production
python seed.py                 # sample delivery rules + 25 license keys + admin + a test checkout URL
uvicorn app.main:app --reload --port 8000
```

`seed.py` prints a ready-to-use **sample checkout URL** — paste it in a browser to exercise the flow without Shopify. Then:
- Admin → http://localhost:8000/admin.html  (login: `admin@store.local` / `admin123`)

The included **Test** payment method auto-confirms and delivers instantly so you can demo the whole flow without any payment keys.

---

## Shopify → checkout handoff

Shopify redirects the buyer to this app with the cart encoded in the URL:

```
/checkout.html?cart=<base64url(JSON)>&sig=<hmac-sha256 hex>
```

The JSON payload Shopify encodes:

```json
{
  "items":    [{ "sku": "TKP-LIFETIME", "name": "Toolkit Pro", "qty": 1, "price_cents": 4900 }],
  "currency": "USD",
  "email":    "buyer@example.com",
  "ref":      "shopify-order-1234"
}
```

Because the price travels in the URL, set **`CART_SIGNING_SECRET`** (same value on both sides) so this app rejects any cart whose signature doesn't match — a buyer can't edit the price they pay. With no secret set (dev), unsigned carts are accepted. Use `app/cart.py:sign_cart()` (or replicate its HMAC-SHA256 over the base64 string) on the Shopify side to mint the link. Each line's `sku` is matched to a **delivery rule** in this app to decide how it's fulfilled.

---

## How delivery works

Each **delivery rule** (admin → *Delivery rules*, keyed by Shopify SKU) has a `delivery_type`:

| Type | What the buyer gets | Admin setup |
|------|--------------------|-------------|
| `license_key` | One unused key pulled from the rule's key pool | Paste keys via **Delivery rules → Keys** |
| `file_download` | A tokenized link, expiring + download-count limited | Set the rule's `download_url` |
| `access_grant` | Rendered text (`{email}`, `{order}` placeholders) | Write an `access_template` |

On a confirmed payment, `delivery.py` matches each line item's SKU to its rule and fulfills it — marks keys used, mints download tokens, renders templates — then emails one confirmation. Keys are flushed per-item so a quantity of 3 hands out 3 *distinct* keys. A line whose SKU has **no** matching rule is still recorded; the order shows in admin for manual delivery.

---

## Payment flow

`payments.py` is a provider abstraction returning `confirmed` (deliver now) or `pending` (await webhook):

- **test** — auto-confirms, dev only (`ENABLE_TEST_PROVIDER`).
- **stripe** — creates a Checkout Session; `/api/webhooks/stripe` confirms + fulfills.
- **crypto** — creates a NowPayments invoice; `/api/webhooks/crypto` confirms + fulfills.

Without API keys, stripe/crypto return a stub `payment_url` so the pending → fulfill path is still demonstrable (use **Orders → Deliver** in admin). Add real keys in `.env` to go live. **Verify webhook signatures before production** — see the NOTE comments in `routers/checkout.py`.

---

## Whop checkout (the live payment path)

The Shopify storefronts redirect buyers to `checkout.html`, which takes payment through **Whop's embedded checkout**. Pricing is dynamic (any cart total) via a single Whop product whose price is overridden per checkout session.

Order lifecycle:

```
Proceed → POST /api/payments/create-whop-session   (mints a Whop session + an order_ref in its metadata)
Pay     → POST /api/orders/create-pending          (records a PENDING order, linked by order_ref)
Paid by EITHER:
  • POST /api/webhooks/whop  → matches order_ref → marks paid + fulfills   (automatic)
  • Admin → Orders → "Mark paid"                                            (manual fallback)
```

**Config** (`.env`): `WHOP_API_KEY`, `WHOP_PRODUCT_ID`, `WHOP_API_BASE` (`https://api.whop.com` live, `https://sandbox-api.whop.com` test), `WHOP_WEBHOOK_SECRET`. The frontend `WHOP_ENV` (`checkout.html`) must match — `sandbox` while testing, `production` when live.

**Sandbox testing:** create a business + a paid one-time product at [sandbox.whop.com](https://sandbox.whop.com), use its `prod_…` + a sandbox `apik_…`, set `WHOP_API_BASE` to the sandbox host, and pay with test card `4242 4242 4242 4242`.

**Webhook setup:** Whop dashboard → Developer → Webhooks → point it at `https://<your-backend>/api/webhooks/whop`, copy the signing secret into `WHOP_WEBHOOK_SECRET`. `localhost` can't receive Whop webhooks — expose it (ngrok) or deploy first; until then use the **Mark paid** button. The handler logs each payload, and matches orders on the `order_ref` carried in the session metadata.

---

## API surface

Public: `POST /api/checkout`, `POST /api/payments/create-whop-session`, `POST /api/orders/create-pending`, `GET /api/orders/{id}`, `GET /api/download/{token}`
Webhooks: `POST /api/webhooks/whop`, `/api/webhooks/stripe`, `/api/webhooks/crypto`
Admin (Bearer JWT): `POST /api/admin/login`, delivery-rule CRUD + `/keys`, `GET /api/admin/orders`, order `/fulfill` (Mark paid) + `/refund`, `GET /api/admin/stats`

Interactive docs at `/docs`.

---

## Going to production

1. Set a strong `SECRET_KEY`, real `ADMIN_PASSWORD`, and a `CART_SIGNING_SECRET` (same value used on the Shopify side) so carts are signature-verified.
2. Point `DATABASE_URL` at MariaDB (`mysql+pymysql://...`) or Postgres (`postgresql+psycopg://...`) and `pip install` the matching driver. The license-key lookup already uses `SELECT ... FOR UPDATE SKIP LOCKED` on non-SQLite engines to prevent two buyers getting the same key under concurrency.
3. Add a real `RESEND_API_KEY` (emails log to console until then).
4. Add Stripe / NowPayments keys + webhook secrets; restrict CORS in `main.py`.
5. Serve behind a reverse proxy; run `uvicorn` with `--workers`.

## Layout

```
backend/
  app/
    main.py        # app, static pages, admin bootstrap
    models.py      # delivery rules, keys, orders, payments, deliveries, customers
    schemas.py     # pydantic
    auth.py        # pbkdf2 + JWT
    cart.py        # sign / verify the cart Shopify hands over in the URL
    payments.py    # test / stripe / crypto
    delivery.py    # fulfillment + email
    routers/       # public (orders + downloads), checkout, admin
  seed.py
  .env.example
frontend/
  checkout.html    # renders the incoming Shopify cart, takes payment, shows receipt
  admin.html       # login, stats, delivery rules + keys, orders
```
