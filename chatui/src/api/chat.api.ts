import {
  ChatResponseSchema,
  ProviderListResponseSchema,
  ConversationDetailResponseSchema,
  ConversationListResponseSchema
} from "../schemas/run_ai.schemas";
import type {
  ChatRequestType,
  ChatResponseType,
  ProviderListResponseType,
  ConversationListResponseType,
  ConversationDetailResponseType,
} from "../schemas/run_ai.schemas";
import axiosInstance from "./axios.config";

const ChatApi = {
  chat: async (data: ChatRequestType): Promise<ChatResponseType> => {
    const response = await axiosInstance.post("/v1/api/chat", data);
    return ChatResponseSchema.parse(response.data);
  },

  providers: async (): Promise<ProviderListResponseType> => {
    const response = await axiosInstance.get("/v1/api/providers");
    return ProviderListResponseSchema.parse(response.data);
  },

  listConversations: async (): Promise<ConversationListResponseType> => {
    const response = await axiosInstance.get("/v1/api/conversations");
    return ConversationListResponseSchema.parse(response.data);
  },

  getConversation: async (
    conversationId: string,
  ): Promise<ConversationDetailResponseType> => {
    const response = await axiosInstance.get(
      `/v1/api/conversations/${conversationId}`,
    );
    return ConversationDetailResponseSchema.parse(response.data);
  },

  deleteConversation: async (conversationId: string): Promise<void> => {
    await axiosInstance.delete(`/v1/api/conversations/${conversationId}`);
  },
  
};

export default ChatApi;


