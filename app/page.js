"use client";

import { useState, useRef, useEffect } from "react";

export default function Home() {
  const [messages, setMessages] = useState([
    {
      role: "assistant",
      content:
        "Namaste! Main aapka task assistant hoon. Aap mujhse:\n- Naya task add karwa sakte ho\n- Kisi date range ka ya overdue tasks ka report maang sakte ho\n- Aaj (ya kisi bhi din) ki leave requests puch sakte ho\n\nBatao, kya karna hai?"
    }
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const bottomRef = useRef(null);
  const fileInputRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function send(text) {
    if (!text.trim()) return;
    const userMsg = { role: "user", content: text };
    const newMessages = [...messages, userMsg];
    setMessages(newMessages);
    setInput("");
    setLoading(true);

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: apiFormat(newMessages) })
      });
      const data = await res.json();
      if (data.error) {
        setMessages((m) => [...m, { role: "assistant", content: "Error: " + data.error }]);
      } else {
        setMessages((m) => [...m, { role: "assistant", content: data.reply || "..." }]);
      }
    } catch (e) {
      setMessages((m) => [...m, { role: "assistant", content: "Connection error, dubara try karo." }]);
    } finally {
      setLoading(false);
    }
  }

  // internal messages have simple string content; API needs Anthropic content-block format
  function apiFormat(msgs) {
    return msgs.map((m) => ({
      role: m.role === "assistant" ? "assistant" : "user",
      content: typeof m.content === "string" ? m.content : m.content
    }));
  }

  async function handleFile(e, kind) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch("/api/upload", { method: "POST", body: fd });
      const data = await res.json();
      if (data.url) {
        send(`${kind === "voice" ? "Voice note" : "Attachment"} upload ho gaya: ${data.url}`);
      }
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  }

  return (
    <div className="app">
      <div className="header">
        <div className="dot" />
        <h1>Task Assistant</h1>
      </div>

      <div className="messages">
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role === "user" ? "user" : "bot"}`}>
            {m.content}
          </div>
        ))}
        {loading && <div className="msg bot typing">likh raha hoon...</div>}
        <div ref={bottomRef} />
      </div>

      <div className="composer">
        <input
          ref={fileInputRef}
          type="file"
          style={{ display: "none" }}
          onChange={(e) => handleFile(e, "attachment")}
        />
        <button
          className="icon-btn"
          title="Attachment"
          onClick={() => fileInputRef.current?.click()}
          disabled={uploading}
        >
          📎
        </button>
        <textarea
          rows={1}
          placeholder="Message likho... jaise 'task add karna hai' ya 'overdue report do'"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send(input);
            }
          }}
        />
        <button className="send-btn" onClick={() => send(input)} disabled={loading || !input.trim()}>
          ➤
        </button>
      </div>
    </div>
  );
}
