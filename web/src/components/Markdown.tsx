import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** Agent replies are Markdown. Raw HTML is never rendered. */
export function Markdown({ text }: { text: string }) {
  return (
    <div className="prose-chat">
      <ReactMarkdown remarkPlugins={[remarkGfm]}
        components={{ a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer noopener" /> }}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
