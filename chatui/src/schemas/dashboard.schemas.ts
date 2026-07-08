import {z} from "zod"

// GET /v1/api/metrics/summary
export const SummarySchema = z.object({
    sinceHours: z.number(),
    totalRequests: z.number(),
    successCount: z.number(),
    errorCount: z.number(),
    errorRate: z.number(),
    avgLatencyMs: z.number().nullable(),
    minLatencyMs: z.number().nullable(),
    maxLatencyMs: z.number().nullable(),
    p50LatencyMs: z.number().nullable(),
    p95LatencyMs: z.number().nullable(),
    p99LatencyMs: z.number().nullable(),
    totalTokens: z.number(),
});
export type SummaryType = z.infer<typeof SummarySchema>;

// GET /v1/api/metrics/latency  ->  { sinceHours, groups: [...] }
export const LatencyGroupSchema = z.object({
    provider: z.string(),
    model: z.string(),
    count: z.number(),
    avgLatencyMs: z.number(),
    minLatencyMs: z.number(),
    maxLatencyMs: z.number(),
    p50LatencyMs: z.number().nullable(),
    p95LatencyMs: z.number().nullable(),
    p99LatencyMs: z.number().nullable(),
});
export const LatencySchema = z.object({
    sinceHours: z.number(),
    groups: z.array(LatencyGroupSchema),
});
export type LatencyType = z.infer<typeof LatencySchema>;

// GET /v1/api/metrics/throughput  ->  { sinceHours, bucket, series: [...] }
export const ThroughputPointSchema = z.object({
    bucket: z.string(),
    total: z.number(),
    errors: z.number(),
    success: z.number(),
});
export const ThroughputSchema = z.object({
    sinceHours: z.number(),
    bucket: z.string(),
    series: z.array(ThroughputPointSchema),
});
export type ThroughputType = z.infer<typeof ThroughputSchema>;

// GET /v1/api/metrics/errors  ->  { sinceHours, bucket, series: [...], breakdown: [...] }
export const ErrorPointSchema = z.object({
    bucket: z.string(),
    total: z.number(),
    errors: z.number(),
    errorRate: z.number(),
});
export const ErrorBreakdownSchema = z.object({
    provider: z.string(),
    model: z.string(),
    errorType: z.string().nullable(),
    count: z.number(),
});
export const ErrorSchema = z.object({
    sinceHours: z.number(),
    bucket: z.string(),
    series: z.array(ErrorPointSchema),
    breakdown: z.array(ErrorBreakdownSchema),
});
export type ErrorType = z.infer<typeof ErrorSchema>;