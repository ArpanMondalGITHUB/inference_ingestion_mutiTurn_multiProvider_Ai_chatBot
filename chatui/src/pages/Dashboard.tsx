import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from "recharts";
import MetricsApi from "../api/metrics.api";
import type {
  SummaryType,
  LatencyType,
  ThroughputType,
  ErrorType,
} from "../schemas/dashboard.schemas";

// Reserved status colors (green = success, red = error) — matching the app theme.
const COLOR_SUCCESS = "#237663";
const COLOR_ERROR = "#a43434";
const COLOR_GRID = "#dce3dc";
const COLOR_AXIS = "#63715f";

const RANGE_OPTIONS = [
  { label: "1h", hours: 1 },
  { label: "6h", hours: 6 },
  { label: "24h", hours: 24 },
  { label: "7d", hours: 168 },
];

const BUCKET_OPTIONS = ["minute", "hour", "day"] as const;
type Bucket = (typeof BUCKET_OPTIONS)[number];

function fmtNum(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString();
}

function fmtMs(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${Math.round(value).toLocaleString()} ms`;
}

function fmtPct(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(2)}%`;
}

// Buckets look like "2026-07-06T14" (hour) / "2026-07-06" (day) / "2026-07-06T14:30" (minute).
function shortBucket(bucket: string): string {
  const timePart = bucket.split("T")[1];
  return timePart ?? bucket;
}

function StatTile({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent?: string;
}) {
  return (
    <div className="rounded-lg border border-[#d2dad2] bg-white px-4 py-3">
      <p className="text-[0.72rem] font-bold uppercase tracking-wide text-[#63715f]">
        {label}
      </p>
      <p
        className="mt-1 text-2xl font-bold"
        style={accent ? { color: accent } : undefined}
      >
        {value}
      </p>
    </div>
  );
}

function Dashboard() {
  const [hours, setHours] = useState(24);
  const [bucket, setBucket] = useState<Bucket>("hour");

  const [summary, setSummary] = useState<SummaryType | null>(null);
  const [latency, setLatency] = useState<LatencyType | null>(null);
  const [throughput, setThroughput] = useState<ThroughputType | null>(null);
  const [errors, setErrors] = useState<ErrorType | null>(null);

  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let ignore = false;

    const load = async () => {
      setIsLoading(true);
      setError("");
      try {
        const [summaryData, latencyData, throughputData, errorData] =
          await Promise.all([
            MetricsApi.getSummary(hours),
            MetricsApi.getLatency(hours),
            MetricsApi.getThroughput(hours, bucket),
            MetricsApi.getErrors(hours, bucket),
          ]);

        if (ignore) return;

        setSummary(summaryData);
        setLatency(latencyData);
        setThroughput(throughputData);
        setErrors(errorData);
      } catch {
        if (!ignore) setError("Could not load metrics.");
      } finally {
        if (!ignore) setIsLoading(false);
      }
    };

    load();

    return () => {
      ignore = true;
    };
  }, [hours, bucket]);

  const throughputSeries = (throughput?.series ?? []).map((point) => ({
    ...point,
    bucketLabel: shortBucket(point.bucket),
  }));

  const errorSeries = (errors?.series ?? []).map((point) => ({
    ...point,
    bucketLabel: shortBucket(point.bucket),
    errorRatePct: point.errorRate * 100,
  }));

  return (
    <main className="min-h-screen bg-[linear-gradient(135deg,rgba(219,235,230,0.96),rgba(238,241,237,0.92)),#eef1ed] p-6">
      <div className="mx-auto flex w-full max-w-[1100px] flex-col gap-6">
        {/* Header */}
        <header className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <p className="text-[0.78rem] font-bold uppercase tracking-wide text-[#63715f]">
              Observability
            </p>
            <h1 className="text-2xl font-bold text-[#17201a]">
              Metrics Dashboard
            </h1>
          </div>
          <Link
            to="/"
            className="rounded-md bg-[#e6ece5] px-4 py-2 font-bold text-[#2f4437] hover:bg-[#dbe4d9]"
          >
            ← Back to chat
          </Link>
        </header>

        {/* Controls */}
        <div className="flex flex-wrap items-center gap-4">
          <div className="flex items-center gap-2">
            <span className="text-[0.78rem] font-bold text-[#4d5c50]">
              Range
            </span>
            <div className="flex overflow-hidden rounded-md border border-[#c9d3ca]">
              {RANGE_OPTIONS.map((option) => (
                <button
                  key={option.hours}
                  type="button"
                  onClick={() => setHours(option.hours)}
                  className={`px-3 py-1.5 text-sm font-semibold ${
                    hours === option.hours
                      ? "bg-[#237663] text-white"
                      : "bg-white text-[#2f4437] hover:bg-[#eef2ec]"
                  }`}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          <div className="flex items-center gap-2">
            <span className="text-[0.78rem] font-bold text-[#4d5c50]">
              Bucket
            </span>
            <select
              value={bucket}
              onChange={(event) => setBucket(event.target.value as Bucket)}
              className="min-h-[38px] rounded-md border border-[#c9d3ca] bg-white px-3 text-[#17201a] outline-none focus:border-[#307a69]"
            >
              {BUCKET_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </div>

          {isLoading ? (
            <span className="text-sm text-[#586658]">Loading…</span>
          ) : null}
        </div>

        {error ? (
          <p className="rounded-md bg-[#fbeaea] px-4 py-3 text-sm text-[#a43434]">
            {error}
          </p>
        ) : null}

        {/* KPI tiles */}
        <section className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
          <StatTile
            label="Total Requests"
            value={fmtNum(summary?.totalRequests)}
          />
          <StatTile
            label="Error Rate"
            value={fmtPct(summary?.errorRate)}
            accent={summary && summary.errorRate > 0 ? COLOR_ERROR : undefined}
          />
          <StatTile
            label="Errors"
            value={fmtNum(summary?.errorCount)}
            accent={summary && summary.errorCount > 0 ? COLOR_ERROR : undefined}
          />
          <StatTile label="Avg Latency" value={fmtMs(summary?.avgLatencyMs)} />
          <StatTile label="p95 Latency" value={fmtMs(summary?.p95LatencyMs)} />
          <StatTile label="Total Tokens" value={fmtNum(summary?.totalTokens)} />
        </section>

        {/* Throughput + Error rate charts */}
        <section className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <div className="rounded-lg border border-[#d2dad2] bg-[#fbfcfa] p-4">
            <h2 className="mb-3 text-base font-bold text-[#1f2c22]">
              Throughput
            </h2>
            {throughputSeries.length === 0 ? (
              <EmptyChart />
            ) : (
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={throughputSeries}>
                  <CartesianGrid strokeDasharray="3 3" stroke={COLOR_GRID} />
                  <XAxis
                    dataKey="bucketLabel"
                    stroke={COLOR_AXIS}
                    fontSize={12}
                  />
                  <YAxis stroke={COLOR_AXIS} fontSize={12} allowDecimals={false} />
                  <Tooltip />
                  <Legend />
                  <Bar
                    dataKey="success"
                    name="Success"
                    stackId="a"
                    fill={COLOR_SUCCESS}
                    radius={[0, 0, 0, 0]}
                  />
                  <Bar
                    dataKey="errors"
                    name="Errors"
                    stackId="a"
                    fill={COLOR_ERROR}
                    radius={[4, 4, 0, 0]}
                  />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>

          <div className="rounded-lg border border-[#d2dad2] bg-[#fbfcfa] p-4">
            <h2 className="mb-3 text-base font-bold text-[#1f2c22]">
              Error Rate (%)
            </h2>
            {errorSeries.length === 0 ? (
              <EmptyChart />
            ) : (
              <ResponsiveContainer width="100%" height={260}>
                <LineChart data={errorSeries}>
                  <CartesianGrid strokeDasharray="3 3" stroke={COLOR_GRID} />
                  <XAxis
                    dataKey="bucketLabel"
                    stroke={COLOR_AXIS}
                    fontSize={12}
                  />
                  <YAxis stroke={COLOR_AXIS} fontSize={12} unit="%" />
                  <Tooltip
                    formatter={(value) => `${Number(value).toFixed(2)}%`}
                  />
                  <Line
                    type="monotone"
                    dataKey="errorRatePct"
                    name="Error rate"
                    stroke={COLOR_ERROR}
                    strokeWidth={2}
                    dot={{ r: 3 }}
                  />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </section>

        {/* Latency by model */}
        <section className="rounded-lg border border-[#d2dad2] bg-[#fbfcfa] p-4">
          <h2 className="mb-3 text-base font-bold text-[#1f2c22]">
            Latency by model
          </h2>
          {(latency?.groups.length ?? 0) === 0 ? (
            <p className="py-6 text-center text-sm text-[#586658]">
              No latency data in this range.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] border-collapse text-sm">
                <thead>
                  <tr className="border-b border-[#dce3dc] text-left text-[#63715f]">
                    <Th>Provider</Th>
                    <Th>Model</Th>
                    <Th align="right">Count</Th>
                    <Th align="right">Avg</Th>
                    <Th align="right">Min</Th>
                    <Th align="right">Max</Th>
                    <Th align="right">p50</Th>
                    <Th align="right">p95</Th>
                    <Th align="right">p99</Th>
                  </tr>
                </thead>
                <tbody>
                  {latency?.groups.map((group) => (
                    <tr
                      key={`${group.provider}-${group.model}`}
                      className="border-b border-[#eef2ec]"
                    >
                      <Td>{group.provider}</Td>
                      <Td>{group.model}</Td>
                      <Td align="right">{fmtNum(group.count)}</Td>
                      <Td align="right">{fmtMs(group.avgLatencyMs)}</Td>
                      <Td align="right">{fmtMs(group.minLatencyMs)}</Td>
                      <Td align="right">{fmtMs(group.maxLatencyMs)}</Td>
                      <Td align="right">{fmtMs(group.p50LatencyMs)}</Td>
                      <Td align="right">{fmtMs(group.p95LatencyMs)}</Td>
                      <Td align="right">{fmtMs(group.p99LatencyMs)}</Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* Error breakdown */}
        {(errors?.breakdown.length ?? 0) > 0 ? (
          <section className="rounded-lg border border-[#d2dad2] bg-[#fbfcfa] p-4">
            <h2 className="mb-3 text-base font-bold text-[#1f2c22]">
              Error breakdown
            </h2>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[480px] border-collapse text-sm">
                <thead>
                  <tr className="border-b border-[#dce3dc] text-left text-[#63715f]">
                    <Th>Provider</Th>
                    <Th>Model</Th>
                    <Th>Error type</Th>
                    <Th align="right">Count</Th>
                  </tr>
                </thead>
                <tbody>
                  {errors?.breakdown.map((row, index) => (
                    <tr
                      key={`${row.provider}-${row.model}-${row.errorType}-${index}`}
                      className="border-b border-[#eef2ec]"
                    >
                      <Td>{row.provider}</Td>
                      <Td>{row.model}</Td>
                      <Td>{row.errorType ?? "—"}</Td>
                      <Td align="right">{fmtNum(row.count)}</Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        ) : null}
      </div>
    </main>
  );
}

function EmptyChart() {
  return (
    <div className="flex h-[260px] items-center justify-center text-sm text-[#586658]">
      No data in this range.
    </div>
  );
}

function Th({
  children,
  align = "left",
}: {
  children: React.ReactNode;
  align?: "left" | "right";
}) {
  return (
    <th
      className={`px-3 py-2 text-[0.72rem] font-bold uppercase tracking-wide ${
        align === "right" ? "text-right" : "text-left"
      }`}
    >
      {children}
    </th>
  );
}

function Td({
  children,
  align = "left",
}: {
  children: React.ReactNode;
  align?: "left" | "right";
}) {
  return (
    <td
      className={`px-3 py-2 text-[#2f4437] ${
        align === "right" ? "text-right tabular-nums" : "text-left"
      }`}
    >
      {children}
    </td>
  );
}

export default Dashboard;
