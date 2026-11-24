// Server-side proxy to the GTMOS API. The API token lives only in server env
// (GTMOS_API_TOKEN); the browser talks to /api/gtmos/* and never sees it.
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const METHODS_WITH_BODY = new Set(["POST", "PATCH", "PUT"]);
// Only forward the resources the UI uses; everything else is a 404 here.
const ALLOWED = /^(campaigns(\/[A-Za-z0-9_\-]{1,64}(\/[A-Za-z0-9_\-/]{1,200})?)?|system\/(llm|graph)|health)$/;

function envelopeError(status: number, code: string, message: string) {
  return Response.json(
    { status: "error", error: { code, message, details: null }, operation_id: null },
    { status },
  );
}

async function forward(req: NextRequest, ctx: RouteContext<"/api/gtmos/[...path]">) {
  const base = process.env.GTMOS_API_URL?.replace(/\/+$/, "");
  const token = process.env.GTMOS_API_TOKEN;
  if (!base || !token) {
    return envelopeError(503, "proxy_not_configured", "GTMOS_API_URL and GTMOS_API_TOKEN must be set on the server");
  }
  const { path } = await ctx.params;
  const joined = path.join("/");
  if (path.some((p) => p === ".." || p === ".") || !ALLOWED.test(joined)) {
    return envelopeError(404, "not_found", "Unknown route");
  }

  const headers: Record<string, string> = { "x-gtmos-token": token, accept: "application/json" };
  let body: string | undefined;
  if (METHODS_WITH_BODY.has(req.method)) {
    body = await req.text();
    headers["content-type"] = "application/json";
  }
  try {
    const upstream = await fetch(`${base}/api/${joined}${req.nextUrl.search}`, {
      method: req.method,
      headers,
      body: body || undefined,
      cache: "no-store",
      signal: AbortSignal.timeout(28_000),
    });
    const text = await upstream.text();
    return new Response(text, {
      status: upstream.status,
      headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return envelopeError(502, "upstream_unavailable", "The GTMOS API did not respond");
  }
}

export { forward as GET, forward as POST, forward as PATCH };
