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

    // A random delivery ID is useful for tracing retries in application logs.
    // The application itself performs idempotency using SHA-256 of the raw MIME.
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
      console.log(`Stored email ${message.from} -> ${message.to}; delivery=${deliveryId}; app=${result.slice(0, 300)}`);
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

    return new Response("OTP mail worker is running. Use /ping to test connection to Koyeb backend.", {
      status: 200,
      headers: { "content-type": "text/plain; charset=utf-8" },
    });
  },
};
