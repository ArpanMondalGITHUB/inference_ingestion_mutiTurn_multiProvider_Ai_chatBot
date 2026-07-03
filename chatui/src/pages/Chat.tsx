import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
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
    <main className="chat-shell">
      {showSidebar && (
        <aside className="conversations-sidebar">
          <div className="sidebar-header">
            <h2>Conversations</h2>
            <button
              className="icon-button"
              onClick={() => setShowSidebar(false)}
              type="button"
              aria-label="Close sidebar"
            >
              ✕
            </button>
          </div>

          {isLoadingConversations ? (
            <p className="sidebar-status">Loading…</p>
          ) : conversations.length === 0 ? (
            <p className="sidebar-status">No conversations yet.</p>
          ) : (
            <ul className="conversation-list">
              {conversations.map((convo) => (
                <li
                  key={convo.conversationId}
                  className={`conversation-item${
                    convo.conversationId === conversationId ? " active" : ""
                  }`}
                >
                  <button
                    className="conversation-resume"
                    onClick={() =>
                      handleResumeConversation(convo.conversationId)
                    }
                    disabled={isResumingConversation}
                    type="button"
                  >
                    <span className="conversation-title">{convo.title}</span>
                    <span className="conversation-meta">
                      {convo.messageCount} msgs ·{" "}
                      {formatRelativeTime(convo.updatedAt)}
                    </span>
                  </button>
                  <button
                    className="icon-button danger"
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
      <section className="chat-panel" aria-label="Multi-turn chat">
        <header className="chat-header">
          <div className="header-start">
            <button
              className="icon-button"
              onClick={() => setShowSidebar((s) => !s)}
              type="button"
              aria-label="Toggle conversation history"
              aria-expanded={showSidebar}
            >
              ☰
            </button>
            <div>
              <p className="eyebrow">
                {currentProvider ? currentProvider.label : "AI Chat"}
              </p>
              <h1>Multi-provider assistant</h1>
              {selectedModel ? (
                <p className="model-meta">{selectedModel}</p>
              ) : null}
            </div>
          </div>
          <button
            className="secondary-button"
            onClick={handleNewChat}
            type="button"
          >
            New chat
          </button>
        </header>

        <div className="messages" aria-live="polite">
          {messages.length === 0 ? (
            <div className="empty-state">
              <h2>Start a conversation</h2>
              <p>
                Ask something, then follow up. The server keeps the recent
                context for this chat.
              </p>
            </div>
          ) : (
            messages.map((message, index) => (
              <article
                className={`message ${message.role === "User" ? "user" : "assistant"}`}
                key={`${message.role}-${index}-${message.content.slice(0, 12)}`}
              >
                <span>{message.role}</span>
                <p>
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

        {error ? <p className="error-message">{error}</p> : null}

        <form className="composer" onSubmit={handleSubmit}>
          <div className="provider-controls" aria-label="AI provider controls">
            <label>
              Provider
              <select
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

            <label>
              Model
              <select
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
            aria-label="Message"
            onChange={(event) => setInput(event.target.value)}
            placeholder="Type a message..."
            type="text"
            value={input}
          />
          <button
            className={isSending ? "abort-button" : undefined}
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
