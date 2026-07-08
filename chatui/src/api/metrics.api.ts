import type{ 
    SummaryType,
    LatencyType,
    ThroughputType,
    ErrorType
} from "../schemas/dashboard.schemas";
import { 
    SummarySchema, 
    LatencySchema,
    ThroughputSchema,
    ErrorSchema
 } from "../schemas/dashboard.schemas";
import axiosInstance from "./axios.config";

export type Bucket = "minute" | "hour" | "day";

const MetricsApi = {
    getSummary: async (h=24):Promise<SummaryType> => {
        const response = await axiosInstance.get(`/v1/api/metrics/summary?since_hours=${h}`);
        return SummarySchema.parse(response.data);
    },
    getLatency: async (h=24): Promise<LatencyType> => {
        const response = await axiosInstance.get(`/v1/api/metrics/latency?since_hours=${h}`);
        return LatencySchema.parse(response.data);

    },
    getThroughput: async (h=24, Bucket = "hour"): Promise<ThroughputType> => {
        const response = await axiosInstance.get(`/v1/api/metrics/throughput?since_hours=${h}&bucket=${Bucket}`);
        return ThroughputSchema.parse(response.data);

    },
    getErrors: async (h = 24, Bucket = "hour"): Promise<ErrorType> => {
        const response = await axiosInstance.get(`/v1/api/metrics/errors?since_hours=${h}&bucket=${Bucket}`);
        return ErrorSchema.parse(response.data);

    },

};
export default  MetricsApi;
