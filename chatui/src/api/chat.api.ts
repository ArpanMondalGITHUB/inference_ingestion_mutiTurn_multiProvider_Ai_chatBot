import {
  ChatResponseSchema,
  ProviderListResponseSchema,
  ConversationDetailResponseSchema,
  ConversationListResponseSchema,
  ChatStreamEventSchema,
} from "../schemas/run_ai.schemas";
import type {
  ChatRequestType,
  ChatResponseType,
  ProviderListResponseType,
  ConversationListResponseType,
  ConversationDetailResponseType,
  ChatStreamEventType,
} from "../schemas/run_ai.schemas";
import axiosInstance from "./axios.config";
import { AXIOS_BASE_URL } from "../config/runtime"

type StreamHandlers = {
  signal?:AbortSignal;
  onEvent:(event:ChatStreamEventType) => void;
  onError:(message:string) => void;
  onDone?:() => void;
};

async function streamChat(
  data: ChatRequestType,
  handlers: StreamHandlers,
): Promise<void> {
  try {
    const response = await fetch(`${AXIOS_BASE_URL}/v1/api/chat/stream`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "accept": "text/event-stream",
      },
      body: JSON.stringify(data),
      signal: handlers.signal,
    });

    if (!response.ok || !response.body) {
      handlers.onError("Could not start chat stream.");
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();

      if (done) {
        break;
      }

      buffer += decoder.decode(value, { stream: true });

      const messages = buffer.split("\n\n");
      buffer = messages.pop() ?? "";

      for (const rawMessage of messages) {
        const event = parseSseMessage(rawMessage);
        if (!event.data) {
          continue;
        }

        const parsed = ChatStreamEventSchema.safeParse(JSON.parse(event.data));
        if (!parsed.success) {
          handlers.onError("Invalid stream event received.");
          continue;
        }

        handlers.onEvent(parsed.data);
      }
    }
  } catch {
    if (!handlers.signal?.aborted) {
      handlers.onError("Chat stream failed.");
    }
  } finally {
    handlers.onDone?.();
  }
}

function parseSseMessage(rawMessage: string): { event?: string; data?: string } {
  const result: { event?: string; data?: string } = {};
  const dataLines: string[] = [];

  for (const line of rawMessage.split("\n")) {
    if (line.startsWith("event:")) {
      result.event = line.slice("event:".length).trim();
    }

    if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trimStart());
    }
  }

  if (dataLines.length > 0) {
    result.data = dataLines.join("\n");
  }

  return result;
}

const ChatApi = {
  chat: async (data: ChatRequestType): Promise<ChatResponseType> => {
    const response = await axiosInstance.post("/v1/api/chat", data);
    return ChatResponseSchema.parse(response.data);
  },
  
  streamChat,

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

