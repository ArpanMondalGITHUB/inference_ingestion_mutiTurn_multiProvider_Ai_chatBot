import { z } from "zod";

export const RoleTypeSchema = z.enum(["User", "Assistant"]);
export type RoleType = z.infer<typeof RoleTypeSchema>;

export const ProviderTypeSchema = z.enum(["anthropic", "openai", "gemini"]);
export type ProviderType = z.infer<typeof ProviderTypeSchema>;

export const ChatMessageSchema = z.object({
  role: RoleTypeSchema,
  content: z.string().min(1),
});
export type ChatMessageType = z.infer<typeof ChatMessageSchema>;

export const ChatRequestSchema = z.object({
  conversationId: z.string().optional(),
  message: z.string().min(1, "Prompt Is Required"),
  provider: ProviderTypeSchema.optional(),
  model: z.string().min(1).optional(),
});
export type ChatRequestType = z.infer<typeof ChatRequestSchema>;

export const ChatResponseSchema = z.object({
  conversationId: z.string(),
  message: ChatMessageSchema,
  provider: ProviderTypeSchema,
  model: z.string(),
});
export type ChatResponseType = z.infer<typeof ChatResponseSchema>;

export const ProviderInfoSchema = z.object({
  id: ProviderTypeSchema,
  label: z.string(),
  defaultModel: z.string(),
  models: z.array(z.string()).min(1),
});
export type ProviderInfoType = z.infer<typeof ProviderInfoSchema>;

export const ProviderListResponseSchema = z.object({
  defaultProvider: ProviderTypeSchema,
  providers: z.array(ProviderInfoSchema),
});
export type ProviderListResponseType = z.infer<
  typeof ProviderListResponseSchema
>;

// ── conversation schemas ──────────────────────────────────────────────────────

export const ConversationSummarySchema = z.object({
  conversationId: z.string(),
  title: z.string(),
  messageCount: z.number().int().nonnegative(),
  provider: z.string(),   // plain string — may be empty on brand-new records
  model: z.string(),
  createdAt: z.string(),
  updatedAt: z.string(),
});
export type ConversationSummaryType = z.infer<typeof ConversationSummarySchema>;


export const ConversationDetailSchema = ConversationSummarySchema.extend({
  messages: z.array(ChatMessageSchema),
});
export type ConversationDetailType = z.infer<typeof ConversationDetailSchema>;


export const ConversationListResponseSchema = z.object({
  conversations: z.array(ConversationSummarySchema),
});
export type ConversationListResponseType = z.infer<typeof ConversationListResponseSchema>;


export const ConversationDetailResponseSchema = z.object({
  conversation: ConversationDetailSchema,
});
export type ConversationDetailResponseType = z.infer<typeof ConversationDetailResponseSchema>;


export const ChatStreamStartSchema = z.object({
  type: z.literal("start"),
  conversationId: z.string(),
  provider: ProviderTypeSchema,
  model: z.string(),
  requestId: z.string(),
});

export const ChatStreamChunkSchema = z.object({
  type: z.literal("chunk"),
  content: z.string(),
});

export const ChatStreamDoneSchema = z.object({
  type: z.literal("done"),
  conversationId: z.string(),
  message: ChatMessageSchema,
  provider: ProviderTypeSchema,
  model: z.string(),
});

export const ChatStreamErrorSchema = z.object({
  type: z.literal("error"),
  message: z.string(),
});

export const ChatStreamEventSchema = z.discriminatedUnion("type", [
  ChatStreamStartSchema,
  ChatStreamChunkSchema,
  ChatStreamDoneSchema,
  ChatStreamErrorSchema,
]);

export type ChatStreamEventType = z.infer<typeof ChatStreamEventSchema>;