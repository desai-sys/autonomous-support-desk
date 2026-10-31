import { FileArchive, UploadCloud } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { api } from "../api";
import { cn } from "../lib";
import type { Artifact, Me } from "../types";
import { Modal } from "./Modal";

const FIELDS: Record<string, { key: string; label: string; placeholder: string }[]> = {
  lambda: [
    { key: "function_name", label: "Function name", placeholder: "auto-generated if empty" },
    { key: "runtime", label: "Runtime", placeholder: "auto-detect (python3.12, nodejs20.x…)" },
    { key: "handler", label: "Handler", placeholder: "auto-detect, e.g. app.lambda_handler" },
  ],
  ec2: [
    { key: "instance_id", label: "Instance ID", placeholder: "i-0abc123def4567890" },
    { key: "run_command", label: "Run command", placeholder: "pip3 install -r requirements.txt && python3 app.py" },
  ],
  ec2_new: [
    { key: "instance_type", label: "Instance type", placeholder: "t3.micro" },
    { key: "run_command", label: "Run command", placeholder: "npm install && npm start" },
  ],
  ecs: [{ key: "service_name", label: "Service name", placeholder: "auto-generated if empty" }],
  s3_static_site: [{ key: "bucket_name", label: "Bucket name", placeholder: "auto-generated if empty" }],
  auto: [],
};

export function UploadDialog({ me, open, onClose, onUploaded }: {
  me: Me; open: boolean; onClose: () => void; onUploaded: (artifact: Artifact, prompt: string) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [target, setTarget] = useState("auto");
  const [options, setOptions] = useState<Record<string, string>>({});
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const pick = (f?: File | null) => {
    if (!f) return;
    if (!f.name.toLowerCase().endsWith(".zip")) return toast.error("Please choose a .zip file.");
    if (f.size > me.max_upload_mb * 1024 * 1024) return toast.error(`The limit is ${me.max_upload_mb} MB.`);
    setFile(f);
  };

  const submit = async () => {
    if (!file) return;
    if (target === "ec2" && !options.instance_id) return toast.error("Enter the instance ID, or choose a new instance.");
    setBusy(true);
    try {
      const artifact = await api.upload(file, target, options);
      const details = Object.entries(options).filter(([, v]) => v).map(([k, v]) => `${k.replace(/_/g, " ")}: ${v}`).join(", ");
      const prompt = target === "auto"
        ? `I uploaded \`${file.name}\`. Inspect it and recommend where to deploy and run it.`
        : target === "ec2_new"
          ? `Launch a new EC2 instance and deploy and run the uploaded \`${file.name}\` on it.`
          : `Deploy and run the uploaded \`${file.name}\` on ${me.deploy_targets[target]}.`;
      onUploaded(artifact, details ? `${prompt} (${details})` : prompt);
      setFile(null); setOptions({}); setTarget("auto");
      onClose();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="Deploy code">
      <div
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); pick(e.dataTransfer.files[0]); }}
        onClick={() => input.current?.click()}
        className={cn("cursor-pointer rounded-2xl border-2 border-dashed p-8 text-center transition",
          drag ? "border-brand-500 bg-brand-500/10" : "hairline hover:border-brand-500/60")}>
        <input ref={input} type="file" accept=".zip" className="hidden" onChange={(e) => pick(e.target.files?.[0])} />
        {file ? (
          <div className="flex items-center justify-center gap-3">
            <FileArchive className="text-brand-400" />
            <div className="text-left">
              <div className="font-semibold">{file.name}</div>
              <div className="text-xs muted">{(file.size / 1024).toFixed(0)} KB · click to change</div>
            </div>
          </div>
        ) : (
          <>
            <UploadCloud className="mx-auto mb-2 text-brand-400" size={32} />
            <div className="font-semibold">Drop a .zip here or click to browse</div>
            <div className="mt-1 text-xs muted">Up to {me.max_upload_mb} MB</div>
          </>
        )}
      </div>

      <div className="mt-5 text-xs font-semibold uppercase tracking-wider muted">Run it on</div>
      <div className="mt-2 grid grid-cols-2 gap-2">
        {Object.entries(me.deploy_targets).map(([key, label]) => (
          <button key={key} onClick={() => { setTarget(key); setOptions({}); }}
            className={cn("rounded-xl border px-3 py-2.5 text-left text-sm transition",
              target === key ? "border-brand-500 bg-brand-500/10 font-semibold" : "hairline hover:border-brand-500/50")}>
            {label}
          </button>
        ))}
      </div>

      {FIELDS[target]?.length > 0 && (
        <div className="mt-4 space-y-3">
          {FIELDS[target].map((f) => (
            <label key={f.key} className="block text-xs font-medium muted">{f.label}
              <input className="input mt-1" placeholder={f.placeholder} value={options[f.key] ?? ""}
                onChange={(e) => setOptions({ ...options, [f.key]: e.target.value })} />
            </label>
          ))}
        </div>
      )}

      <div className="mt-6 flex justify-end gap-2">
        <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" disabled={!file || busy} onClick={submit}>
          {busy ? "Uploading…" : "Upload & send to agent"}
        </button>
      </div>
    </Modal>
  );
}
