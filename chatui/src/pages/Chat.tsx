import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { Link } from "react-router-dom";
import type {
  ChatMessageType,
  ProviderInfoType,
  ProviderType,
  ConversationSummaryType,
} from "../schemas/run_ai.schemas";
import ChatApi from "../api/chat.api";

function formatRelativeTime(isoString: string): string {
  const diffMs = Date.now() - new Date(isoString).getTime();
  const diffMin = Math.floor(diffMs / 60_000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24) return `${diffH}h ago`;
  return `${Math.floor(diffH / 24)}d ago`;
}

function Chat() {
  const [messages, setMessages] = useState<ChatMessageType[]>([]);
  const [conversationId, setConversationId] = useState<string>();
  const [input, setInput] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState("");
  const [providers, setProviders] = useState<ProviderInfoType[]>([]);
  const [selectedProvider, setSelectedProvider] = useState<ProviderType>("gemini");
  const [selectedModel, setSelectedModel] = useState("");

  const [showSidebar, setShowSidebar] = useState(false);
  const [conversations, setConversations] = useState<ConversationSummaryType[]>([],);
  const [isLoadingConversations, setIsLoadingConversations] = useState(false);
  const [isResumingConversation, setIsResumingConversation] = useState(false);
  const streamAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => {
      streamAbortRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    let ignore = false;

    const loadProviders = async () => {
      try {
        const response = await ChatApi.providers();
        if (ignore) {
          return;
        }

        setProviders(response.providers);

        const defaultProvider =
          response.providers.find(
            (provider) => provider.id === response.defaultProvider,
          ) ?? response.providers[0];

        if (defaultProvider) {
          setSelectedProvider(defaultProvider.id);
          setSelectedModel(defaultProvider.defaultModel);
        } else {
          setError("No AI providers are configured.");
        }
      } catch {
        if (!ignore) {
          setError("Could not load AI providers.");
        }
      }
    };

    loadProviders();

    return () => {
      ignore = true;
    };
  }, []);

  const loadConversations = useCallback(async () => {
    setIsLoadingConversations(true);
    try {
      const response = await ChatApi.listConversations();
      setConversations(response.conversations);
    } catch {
      // sidebar stays empty on failure; don't overwrite the main chat error
    } finally {
      setIsLoadingConversations(false);
    }
  }, []);

  useEffect(() => {
    if (showSidebar) loadConversations();
  }, [showSidebar, loadConversations]);

  const currentProvider = useMemo(
    () => providers.find((provider) => provider.id === selectedProvider),
    [providers, selectedProvider],
  );

  const availableModels = currentProvider?.models ?? [];

  const canSend =
    input.trim().length > 0 &&
    !isSending &&
    Boolean(currentProvider) &&
    selectedModel.length > 0;

  const handleProviderChange = (providerId: ProviderType) => {
    const nextProvider = providers.find(
      (provider) => provider.id === providerId,
    );

    setSelectedProvider(providerId);
    setSelectedModel(
      nextProvider?.defaultModel ?? nextProvider?.models[0] ?? "",
    );
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    const message = input.trim();
    if (!canSend) {
      return;
    }

    const userMessage: ChatMessageType = {
      role: "User",
      content: message,
    };

    const assistantMessage: ChatMessageType = {
      role: "Assistant",
      content: "",
    };

    setMessages((currentMessages) => [
      ...currentMessages,
      userMessage,
      assistantMessage,
    ]);
    setInput("");
    setError("");
    setIsSending(true);

    streamAbortRef.current?.abort();
    const controller = new AbortController();
    streamAbortRef.current = controller;
    const isCurrentStream = () => streamAbortRef.current === controller;

    await ChatApi.streamChat(
      {
        conversationId,
        message,
        provider: selectedProvider,
        model: selectedModel,
      },
      {
        signal: controller.signal,
        onEvent: (event) => {
          if (!isCurrentStream()) {
            return;
          }

          if (event.type === "start") {
            setConversationId(event.conversationId);
            return;
          }

          if (event.type === "chunk") {
            setMessages((currentMessages) => {
              const next = [...currentMessages];
              const last = next[next.length - 1];

              if (last?.role === "Assistant") {
                next[next.length - 1] = {
                  ...last,
                  content: last.content + event.content,
                };
              }

              return next;
            });
            return;
          }

          if (event.type === "done") {
            setConversationId(event.conversationId);
            setMessages((currentMessages) => {
              const next = [...currentMessages];
              const last = next[next.length - 1];

              if (last?.role === "Assistant") {
                next[next.length - 1] = event.message;
              }

              return next;
            });
            return;
          }

          if (event.type === "error") {
            setError(event.message);
          }
        },
        onError: (message) => {
          if (isCurrentStream()) {
            setError(message);
          }
        },
        onDone: () => {
          if (!isCurrentStream()) {
            return;
          }

          setIsSending(false);
          streamAbortRef.current = null;
          setMessages((currentMessages) => {
            const last = currentMessages[currentMessages.length - 1];

            if (last?.role === "Assistant" && last.content.length === 0) {
              return currentMessages.slice(0, -1);
            }

            return currentMessages;
          });
        },
      },
    );
  };

  const handleCancelStream = () => {
    streamAbortRef.current?.abort();
    streamAbortRef.current = null;
    setIsSending(false);
    setMessages((currentMessages) => {
      const last = currentMessages[currentMessages.length - 1];

      if (last?.role === "Assistant" && last.content.length === 0) {
        return currentMessages.slice(0, -1);
      }

      return currentMessages;
    });
  };

  const handleNewChat = () => {
    setMessages([]);
    setConversationId(undefined);
    setInput("");
    setError("");
  };

  const handleResumeConversation = async (id: string) => {
    setIsResumingConversation(true);
    setError("");
    try {
      const { conversation } = await ChatApi.getConversation(id);
      const provider = providers.find(
        (item) => item.id === conversation.provider,
      );
      if (provider) {
        setSelectedProvider(provider.id);
        setSelectedModel(
          provider.models.includes(conversation.model)
            ? conversation.model
            : provider.defaultModel,
        );
      }
      setConversationId(conversation.conversationId);
      setMessages(conversation.messages);
      setInput("");
      setShowSidebar(false);
    } catch {
      setError("Could not load that conversation.");
    } finally {
      setIsResumingConversation(false);
    }
  };

  const handleCancelConversation = async (id: string) => {
    try {
      await ChatApi.deleteConversation(id);
      setConversations((prev) => prev.filter((c) => c.conversationId !== id));
      if (id === conversationId) {
        setMessages([]);
        setConversationId(undefined);
        setInput("");
        setError("");
      }
    } catch {
      setError("Could not cancel that conversation.");
    }
  };

  return (
    <main className="flex items-center min-h-screen p-6 max-sm:p-0 bg-[#eef1ed] bg-[image:linear-gradient(135deg,rgba(219,235,230,0.96),rgba(238,241,237,0.92))]">
      {showSidebar && (
        <aside className="flex h-screen w-[280px] flex-shrink-0 flex-col overflow-y-auto border-r border-[#e5e7eb]">
          <div className="flex items-center justify-between border-b border-[#e5e7eb] p-4">
            <h2 className="m-0 text-[0.95rem] font-semibold">Conversations</h2>
            <button
              className="cursor-pointer rounded-md border-none bg-transparent p-[0.4rem] text-base leading-none text-inherit hover:bg-[#f3f4f6]"
              onClick={() => setShowSidebar(false)}
              type="button"
              aria-label="Close sidebar"
            >
              ✕
            </button>
          </div>

          {isLoadingConversations ? (
            <p className="p-4 text-[0.85rem] text-[#6b7280]">Loading…</p>
          ) : conversations.length === 0 ? (
            <p className="p-4 text-[0.85rem] text-[#6b7280]">No conversations yet.</p>
          ) : (
            <ul className="m-0 list-none px-0 py-2">
              {conversations.map((convo) => (
                <li
                  key={convo.conversationId}
                  className={`flex items-stretch gap-1 px-2 py-1${
                    convo.conversationId === conversationId ? " bg-[#f3f4f6]" : ""
                  }`}
                >
                  <button
                    className="flex flex-1 cursor-pointer flex-col gap-[0.2rem] rounded-md border-none bg-transparent p-2 text-left hover:bg-[#f3f4f6] disabled:cursor-not-allowed disabled:opacity-50"
                    onClick={() =>
                      handleResumeConversation(convo.conversationId)
                    }
                    disabled={isResumingConversation}
                    type="button"
                  >
                    <span className="max-w-[190px] overflow-hidden text-ellipsis whitespace-nowrap text-[0.875rem] font-medium">{convo.title}</span>
                    <span className="text-[0.75rem] text-[#9ca3af]">
                      {convo.messageCount} msgs ·{" "}
                      {formatRelativeTime(convo.updatedAt)}
                    </span>
                  </button>
                  <button
                    className="cursor-pointer rounded-md border-none bg-transparent p-[0.4rem] text-base leading-none text-inherit hover:bg-[#fee2e2] hover:text-[#dc2626]"
                    onClick={() =>
                      handleCancelConversation(convo.conversationId)
                    }
                    type="button"
                    aria-label={`Delete: ${convo.title}`}
                  >
                    🗑
                  </button>
                </li>
              ))}
            </ul>
          )}
        </aside>
      )}
      <section
        className="flex flex-col w-full max-w-[920px] mx-auto h-[min(760px,calc(100vh_-_48px))] overflow-hidden rounded-[8px] border border-[#d2dad2] bg-[#fbfcfa] shadow-[0_24px_70px_rgba(23,32,26,0.12)] max-sm:h-screen max-sm:rounded-none max-sm:border-0"
        aria-label="Multi-turn chat"
      >
        <header className="flex items-center justify-between gap-4 border-b border-[#dce3dc] px-5 py-[18px] max-sm:flex-col max-sm:items-start">
          <div className="flex items-center gap-3">
            <button
              className="cursor-pointer rounded-md border-none bg-transparent p-[0.4rem] text-base leading-none text-inherit hover:bg-[#f3f4f6]"
              onClick={() => setShowSidebar((s) => !s)}
              type="button"
              aria-label="Toggle conversation history"
              aria-expanded={showSidebar}
            >
              ☰
            </button>
            <div>
              <p className="m-0 mb-1 text-[0.78rem] font-bold uppercase text-[#63715f]">
                {currentProvider ? currentProvider.label : "AI Chat"}
              </p>
              <h1 className="m-0 text-[1.3rem] font-[750]">Multi-provider assistant</h1>
              {selectedModel ? (
                <p className="m-0 mt-1 text-[0.82rem] text-[#586658]">{selectedModel}</p>
              ) : null}
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Link
              className="inline-flex items-center min-h-[42px] cursor-pointer rounded-md border-0 bg-[#e6ece5] px-[14px] font-bold text-[#2f4437] no-underline"
              to="/dashboard"
            >
              Dashboard
            </Link>
            <button
              className="min-h-[42px] cursor-pointer rounded-md border-0 bg-[#e6ece5] px-[14px] font-bold text-[#2f4437]"
              onClick={handleNewChat}
              type="button"
            >
              New chat
            </button>
          </div>
        </header>

        <div className="flex flex-1 flex-col gap-3 overflow-y-auto p-5" aria-live="polite">
          {messages.length === 0 ? (
            <div className="m-auto max-w-[440px] self-center text-center text-[#586658]">
              <h2 className="mb-2 text-[1.45rem] text-[#1f2c22]">Start a conversation</h2>
              <p className="m-0">
                Ask something, then follow up. The server keeps the recent
                context for this chat.
              </p>
            </div>
          ) : (
            messages.map((message, index) => (
              <article
                className={`max-w-[min(76%,640px)] whitespace-pre-wrap break-words rounded-[8px] border px-[14px] py-3 leading-[1.5] [&>span]:mb-[5px] [&>span]:block [&>span]:text-[0.74rem] [&>span]:font-[750] [&>span]:uppercase ${
                  message.role === "User"
                    ? "self-end border-[#123c34] bg-[#123c34] text-white [&>span]:text-[#b8d9d0]"
                    : "self-start border-[#dbe3dc] bg-white [&>span]:text-[#63715f]"
                }`}
                key={`${message.role}-${index}-${message.content.slice(0, 12)}`}
              >
                <span>{message.role}</span>
                <p className="m-0">
                  {message.content ||
                    (isSending &&
                    index === messages.length - 1 &&
                    message.role === "Assistant"
                      ? "Thinking..."
                      : "")}
                </p>
              </article>
            ))
          )}
        </div>

        {error ? <p className="m-0 px-5 pb-3 text-[0.92rem] text-[#a43434]">{error}</p> : null}

        <form
          className="flex items-end gap-[10px] border-t border-[#dce3dc] p-[14px] max-sm:flex-col max-sm:items-stretch"
          onSubmit={handleSubmit}
        >
          <div
            className="flex items-end gap-[10px] max-sm:w-full max-sm:flex-col max-sm:items-stretch"
            aria-label="AI provider controls"
          >
            <label className="grid gap-1 text-[0.78rem] font-bold text-[#4d5c50]">
              Provider
              <select
                className="min-h-[46px] min-w-[150px] rounded-md border border-[#c9d3ca] bg-white px-[10px] text-[#17201a] outline-none focus:border-[#307a69] focus:shadow-[0_0_0_3px_rgba(48,122,105,0.14)] max-sm:w-full"
                disabled={isSending || providers.length === 0}
                onChange={(event) =>
                  handleProviderChange(event.target.value as ProviderType)
                }
                value={selectedProvider}
              >
                {providers.map((provider) => (
                  <option key={provider.id} value={provider.id}>
                    {provider.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="grid gap-1 text-[0.78rem] font-bold text-[#4d5c50]">
              Model
              <select
                className="min-h-[46px] min-w-[150px] rounded-md border border-[#c9d3ca] bg-white px-[10px] text-[#17201a] outline-none focus:border-[#307a69] focus:shadow-[0_0_0_3px_rgba(48,122,105,0.14)] max-sm:w-full"
                disabled={isSending || availableModels.length === 0}
                onChange={(event) => setSelectedModel(event.target.value)}
                value={selectedModel}
              >
                {availableModels.map((model) => (
                  <option key={model} value={model}>
                    {model}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <input
            className="min-h-[46px] min-w-0 flex-1 rounded-md border border-[#c9d3ca] px-[14px] outline-none focus:border-[#307a69] focus:shadow-[0_0_0_3px_rgba(48,122,105,0.14)]"
            aria-label="Message"
            onChange={(event) => setInput(event.target.value)}
            placeholder="Type a message..."
            type="text"
            value={input}
          />
          <button
            className={`min-h-[42px] cursor-pointer rounded-md border-0 px-5 font-bold text-white disabled:cursor-not-allowed disabled:opacity-[0.55] ${
              isSending ? "bg-[#a43434]" : "bg-[#237663]"
            }`}
            disabled={isSending ? false : !canSend}
            onClick={isSending ? handleCancelStream : undefined}
            type={isSending ? "button" : "submit"}
          >
            {isSending ? "Abort" : "Send"}
          </button>
        </form>
      </section>
    </main>
  );
}

export default Chat;
