/**
 * OTP Inbox Cloudflare Worker
 * - email(): ingest inbound mail → POST APP_INGEST_URL
 * - fetch /send: outbound send via Email Service binding (env.EMAIL)
 * - fetch /ping + scheduled: keep-alive for PaaS backends
 */

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function unauthorized() {
  return json({ ok: false, error: "unauthorized" }, 401);
}

function requireWorkerToken(request, env) {
  const token = request.headers.get("X-Worker-Token") || "";
  return Boolean(env.WORKER_INGEST_TOKEN && token === env.WORKER_INGEST_TOKEN);
}

function normalizeAddrList(value) {
  if (value == null || value === "") return [];
  const list = Array.isArray(value) ? value : [value];
  return list
    .map((item) => {
      if (typeof item === "string") return item.trim();
      if (item && typeof item === "object" && item.email) {
        return item.name
          ? { email: String(item.email).trim(), name: String(item.name).trim() }
          : String(item.email).trim();
      }
      return "";
    })
    .filter(Boolean);
}

async function handleSend(request, env) {
  if (!requireWorkerToken(request, env)) return unauthorized();
  if (!env.EMAIL || typeof env.EMAIL.send !== "function") {
    return json(
      {
        ok: false,
        error:
          "send_email binding missing. Add [[send_email]] name = \"EMAIL\" to wrangler.toml and onboard the domain in Email Service.",
        code: "E_SEND_NOT_CONFIGURED",
      },
      503,
    );
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ ok: false, error: "invalid JSON body" }, 400);
  }

  const from = body.from;
  const to = normalizeAddrList(body.to);
  const cc = normalizeAddrList(body.cc);
  const bcc = normalizeAddrList(body.bcc);
  const subject = String(body.subject || "").slice(0, 998);
  const text = body.text != null ? String(body.text) : undefined;
  const html = body.html != null ? String(body.html) : undefined;
  const replyTo = body.reply_to || body.replyTo || undefined;

  if (!from || !to.length) {
    return json({ ok: false, error: "from and to are required", code: "E_FIELD_MISSING" }, 400);
  }
  if (!subject) {
    return json({ ok: false, error: "subject is required", code: "E_FIELD_MISSING" }, 400);
  }
  if (!text && !html) {
    return json({ ok: false, error: "text or html body is required", code: "E_FIELD_MISSING" }, 400);
  }
  if (to.length + cc.length + bcc.length > 50) {
    return json({ ok: false, error: "too many recipients (max 50)", code: "E_TOO_MANY_RECIPIENTS" }, 400);
  }

  const payload = {
    from,
    to: to.length === 1 ? to[0] : to,
    subject,
  };
  if (cc.length) payload.cc = cc.length === 1 ? cc[0] : cc;
  if (bcc.length) payload.bcc = bcc.length === 1 ? bcc[0] : bcc;
  if (replyTo) payload.replyTo = replyTo;
  if (text) payload.text = text;
  if (html) payload.html = html;

  if (Array.isArray(body.attachments) && body.attachments.length) {
    if (body.attachments.length > 32) {
      return json({ ok: false, error: "too many attachments (max 32)", code: "E_TOO_MANY_ATTACHMENTS" }, 400);
    }
    payload.attachments = body.attachments.map((a) => ({
      content: a.content || a.content_base64 || "",
      filename: String(a.filename || "attachment"),
      type: String(a.type || a.content_type || "application/octet-stream"),
      disposition: a.disposition === "inline" ? "inline" : "attachment",
      contentId: a.contentId || a.content_id || undefined,
    }));
  }

  try {
    const result = await env.EMAIL.send(payload);
    return json({
      ok: true,
      messageId: result?.messageId || result?.message_id || null,
    });
  } catch (error) {
    const code = error?.code || "E_DELIVERY_FAILED";
    const message = error?.message || String(error);
    console.error("EMAIL.send failed", code, message);
    const status =
      code === "E_RATE_LIMIT_EXCEEDED" || code === "E_DAILY_LIMIT_EXCEEDED"
        ? 429
        : code === "E_SENDER_NOT_VERIFIED" ||
            code === "E_SENDER_DOMAIN_NOT_AVAILABLE" ||
            code === "E_VALIDATION_ERROR" ||
            code === "E_FIELD_MISSING" ||
            code === "E_TOO_MANY_RECIPIENTS" ||
            code === "E_CONTENT_TOO_LARGE" ||
            code === "E_RECIPIENT_NOT_ALLOWED"
          ? 400
          : 502;
    return json({ ok: false, error: message, code }, status);
  }
}

export default {
  async email(message, env, ctx) {
    const ingestUrl = env.APP_INGEST_URL || env.INGEST_URL;
    const workerToken = env.WORKER_INGEST_TOKEN;

    if (!ingestUrl || !workerToken) {
      message.setReject("OTP inbox is not configured");
      return;
    }

    const cleanHeader = (value, max = 1800) =>
      String(value || "")
        .replace(/[\r\n]/g, " ")
        .slice(0, max);

    const deliveryId = crypto.randomUUID();

    const headers = new Headers();
    headers.set("Content-Type", "message/rfc822");
    headers.set("X-Worker-Token", workerToken);
    headers.set("X-Worker-Delivery-ID", deliveryId);
    headers.set("X-Worker-Received-At", new Date().toISOString());
    headers.set("X-Envelope-From", cleanHeader(message.from));
    headers.set("X-Envelope-To", cleanHeader(message.to));
    headers.set("X-Email-Subject", cleanHeader(message.headers.get("subject")));
    headers.set("X-Email-Message-ID", cleanHeader(message.headers.get("message-id")));
    headers.set("X-Email-Date", cleanHeader(message.headers.get("date")));
    headers.set("X-Cloudflare-Raw-Size", String(message.rawSize || 0));

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30000);

    try {
      const response = await fetch(ingestUrl, {
        method: "POST",
        headers,
        body: message.raw,
        signal: controller.signal,
      });

      if (!response.ok) {
        const errorText = await response.text();
        console.error("OTP app rejected email", response.status, errorText.slice(0, 1000));
        message.setReject("OTP inbox temporarily unavailable");
        return;
      }

      const result = await response.text();
      console.log(
        `Stored email ${message.from} -> ${message.to}; delivery=${deliveryId}; app=${result.slice(0, 300)}`,
      );
    } catch (error) {
      console.error("OTP app ingest failed", deliveryId, error);
      message.setReject("OTP inbox temporarily unavailable");
    } finally {
      clearTimeout(timeout);
    }
  },

  async scheduled(controller, env, ctx) {
    const ingestUrl = env.APP_INGEST_URL || env.INGEST_URL;
    if (!ingestUrl) return;

    try {
      const pingUrl = new URL("/health", ingestUrl).toString();
      const res = await fetch(pingUrl, {
        headers: { "User-Agent": "Cloudflare-Worker-KeepAlive/1.0" },
      });
      console.log(`[KeepAlive] Pinged ${pingUrl} - status: ${res.status}`);
    } catch (err) {
      console.error("[KeepAlive] Ping failed:", err);
    }
  },

  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (url.pathname === "/send" && request.method === "POST") {
      return handleSend(request, env);
    }

    if (url.pathname === "/send" && request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: {
          "access-control-allow-origin": "*",
          "access-control-allow-methods": "POST, OPTIONS",
          "access-control-allow-headers": "content-type, x-worker-token",
        },
      });
    }

    if (url.pathname === "/ping") {
      const ingestUrl = env.APP_INGEST_URL || env.INGEST_URL;
      if (!ingestUrl) {
        return new Response("APP_INGEST_URL / INGEST_URL is not configured", { status: 500 });
      }
      try {
        const pingUrl = new URL("/health", ingestUrl).toString();
        const start = Date.now();
        const res = await fetch(pingUrl, {
          headers: { "User-Agent": "Cloudflare-Worker-KeepAlive/1.0" },
        });
        const duration = Date.now() - start;
        const text = await res.text();
        return new Response(`Pinged ${pingUrl} in ${duration}ms\nStatus: ${res.status}\nResponse: ${text}`, {
          status: res.status,
          headers: { "content-type": "text/plain; charset=utf-8" },
        });
      } catch (err) {
        return new Response(`Ping failed: ${err.message}`, {
          status: 502,
          headers: { "content-type": "text/plain; charset=utf-8" },
        });
      }
    }

    return new Response(
      "OTP mail worker is running.\n- POST /send  (outbound via Email Service)\n- GET  /ping  (backend health)\n",
      { status: 200, headers: { "content-type": "text/plain; charset=utf-8" } },
    );
  },
};
