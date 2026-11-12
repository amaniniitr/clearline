import { NextRequest, NextResponse } from "next/server";
import dbData from "@/data/db.json";

const db = dbData as any;

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ slug: string[] }> }
) {
  const { slug } = await params;
  const path = "/api/" + slug.join("/");

  // 1. Direct path matches
  if (db[path]) {
    return NextResponse.json(db[path]);
  }

  // 2. /api/runs/:id/events (SSE stream)
  if (slug[0] === "runs" && slug[2] === "events") {
    const runId = slug[1];
    const runDetail = db.runs_detail?.[runId] || Object.values(db.runs_detail || {})[0];
    const events = (runDetail as any)?.events || [];

    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      async start(controller) {
        for (const ev of events) {
          controller.enqueue(encoder.encode(`data: ${JSON.stringify(ev)}\n\n`));
          await new Promise((r) => setTimeout(r, 60));
        }
        controller.close();
      },
    });

    return new Response(stream, {
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache, no-transform",
        Connection: "keep-alive",
      },
    });
  }

  // 3. /api/runs/:id
  if (slug[0] === "runs" && slug[1]) {
    const runId = slug[1];
    const runDetail = db.runs_detail?.[runId];
    if (runDetail) {
      return NextResponse.json(runDetail);
    }
    // Fallback to first run detail if not found
    const first = Object.values(db.runs_detail || {})[0];
    if (first) return NextResponse.json(first);
    return NextResponse.json({ error: "Run not found" }, { status: 404 });
  }

  return NextResponse.json({ error: "Not found", path }, { status: 404 });
}

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ slug: string[] }> }
) {
  const { slug } = await params;

  // 1. /api/scenarios/:id/run
  if (slug[0] === "scenarios" && slug[2] === "run") {
    const scId = slug[1];
    // Find matching run in db.runs
    const runs = db["/api/runs"] || [];
    const matched = runs.find((r: any) => r.scenario === scId) || runs[0];
    const runId = matched ? matched.id : "R-261112-04fb15";
    return NextResponse.json({ run_id: runId, redelivery: false });
  }

  // 2. /api/proposals/:id/decisions
  if (slug[0] === "proposals" && slug[2] === "decisions") {
    try {
      const body = await req.json();
      if (body.action === "verify_bank" && body.role !== "controller") {
        return NextResponse.json(
          { detail: "Only a controller may verify bank changes" },
          { status: 403 }
        );
      }
      if (body.action === "short_pay") {
        return NextResponse.json({
          ok: true,
          bill_id: "B-1009",
          amount: body.amount || 6000,
          read_back_ok: true,
        });
      }
      return NextResponse.json({
        ok: true,
        status: "approved",
        bill_id: "B-1010",
        read_back_ok: true,
      });
    } catch {
      return NextResponse.json({ ok: true, status: "approved" });
    }
  }

  // 3. /api/demo/grn-883
  if (slug[0] === "demo" && slug[1] === "grn-883") {
    return NextResponse.json({
      ok: true,
      rematched_runs: ["R-261112-04fb15"],
    });
  }

  // 4. /api/admin/reset
  if (slug[0] === "admin" && slug[1] === "reset") {
    return NextResponse.json({ ok: true });
  }

  // 5. /api/upload
  if (slug[0] === "upload") {
    const runs = db["/api/runs"] || [];
    return NextResponse.json({
      run_id: runs[0]?.id || "R-261112-04fb15",
      redelivery: false,
    });
  }

  return NextResponse.json({ ok: true });
}
