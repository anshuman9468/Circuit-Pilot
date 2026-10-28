"use client";

import Editor from "react-simple-code-editor";
import Prism from "prismjs";
import "prismjs/components/prism-json";

type CodeEditorProps = {
  value: string;
  onChange: (value: string) => void;
  filePath: string;
};

const hdlKeywords = /\b(?:module|endmodule|input|output|inout|wire|logic|reg|always_ff|always_comb|always|assign|begin|end|if|else|case|endcase|parameter|localparam|posedge|negedge|for|generate|genvar|function|endfunction|task|endtask|interface|endinterface)\b/g;

function highlight(value: string, filePath: string): string {
  const language = filePath.toLowerCase().endsWith(".json") ? Prism.languages.json : Prism.languages.clike;
  let highlighted = Prism.highlight(value, language, filePath.endsWith(".json") ? "json" : "clike");
  if (!filePath.toLowerCase().endsWith(".json")) {
    highlighted = highlighted.replace(hdlKeywords, '<span class="token keyword">$&</span>');
  }
  return highlighted;
}

export default function CodeEditor({ value, onChange, filePath }: CodeEditorProps) {
  const lineCount = Math.max(1, value.split("\n").length);
  return (
    <div className="code-editor" aria-label={`Source editor for ${filePath}`}>
      <div className="line-numbers" aria-hidden="true">
        {Array.from({ length: lineCount }, (_, index) => index + 1).join("\n")}
      </div>
      <div className="code-editor-inner">
        <Editor
          value={value}
          onValueChange={onChange}
          highlight={(code) => highlight(code, filePath)}
          padding="16px 18px 16px 0"
          textareaId="source-editor"
          textareaClassName="source-editor-input"
          spellCheck={false}
          aria-label={`Edit ${filePath}`}
        />
      </div>
    </div>
  );
}
