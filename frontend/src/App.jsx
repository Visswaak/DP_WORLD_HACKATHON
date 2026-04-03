import { Component, useState } from "react";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

const initialPipeline = [
  { name: "Upload", status: "idle" },
  { name: "OCR extraction", status: "idle" },
  { name: "AI classification", status: "idle" },
  { name: "Compliance engine", status: "idle" },
  { name: "Prediction", status: "idle" },
];

class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, message: "", attempt: 0 };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, message: error?.message ?? "An unexpected error occurred." };
  }

  recover() {
    // Incrementing `attempt` changes the key on children, forcing a full remount
    // so persistent render faults (stale closures, bad state) are cleared.
    this.setState((s) => ({ hasError: false, message: "", attempt: s.attempt + 1 }));
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex min-h-screen items-center justify-center text-sand">
          <div className="glass rounded-[2rem] border border-white/10 p-8 text-center">
            <h2 className="font-display text-2xl text-white">Something went wrong</h2>
            <p className="mt-3 text-sand/70">{this.state.message}</p>
            <button
              className="mt-6 rounded-full bg-ember px-5 py-3 font-semibold text-ink-950 transition hover:brightness-110"
              onClick={() => this.recover()}
            >
              Try again
            </button>
          </div>
        </div>
      );
    }
    return (
      <div key={this.state.attempt}>
        {this.props.children}
      </div>
    );
  }
}

function App() {
  const [files, setFiles] = useState([]);
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [pipeline, setPipeline] = useState(initialPipeline);

  const advancePipeline = (activeIndex) => {
    setPipeline(
      initialPipeline.map((stage, index) => ({
        ...stage,
        status: index < activeIndex ? "completed" : index === activeIndex ? "running" : "idle",
      })),
    );
  };

  const onSubmit = async (event) => {
    event.preventDefault();
    if (loading) return;
    if (!files.length) {
      setError("Select at least one shipment document.");
      return;
    }

    setError("");
    setLoading(true);
    setResults([]);

    const stageDelays = [0, 400, 900, 1500, 2200];
    const timers = stageDelays.map((delay, index) =>
      setTimeout(() => advancePipeline(index), delay),
    );

    const formData = new FormData();
    files.forEach((file) => formData.append("files", file));

    let pollTimer = null;

    try {
      // POST returns immediately with a job_id
      const response = await fetch(`${API_URL}/api/analyze`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        let detail = `Server error (${response.status})`;
        if (response.status === 413) detail = "Files are too large. Reduce file sizes and try again.";
        else if (response.status === 422) detail = "Invalid request format.";
        else if (response.status >= 500) detail = "The server encountered an error. Please try again.";
        try {
          const errBody = await response.json();
          if (errBody?.detail) detail = errBody.detail;
        } catch (_) {}
        throw new Error(detail);
      }

      const { job_id } = await response.json();

      // Poll GET /api/jobs/{job_id} every 2 seconds until done or failed
      await new Promise((resolve, reject) => {
        const poll = () => {
          pollTimer = setTimeout(async () => {
            try {
              const res = await fetch(`${API_URL}/api/jobs/${job_id}`);
              if (!res.ok) throw new Error("Failed to fetch job status.");
              const job = await res.json();

              if (job.status === "done") {
                timers.forEach(clearTimeout);
                setPipeline(initialPipeline.map((s) => ({ ...s, status: "completed" })));
                setResults(job.result ?? []);
                resolve();
              } else if (job.status === "failed") {
                reject(new Error(job.error || "Analysis failed on the server."));
              } else {
                poll(); // queued or processing — keep polling
              }
            } catch (err) {
              reject(err);
            }
          }, 2000);
        };
        poll();
      });

    } catch (requestError) {
      timers.forEach(clearTimeout);
      if (pollTimer) clearTimeout(pollTimer);
      setError(requestError.message || "Unable to analyze documents. Check your connection and try again.");
      setPipeline(initialPipeline);
    } finally {
      setLoading(false);
    }
  };

  const latest = results[0];

  return (
    <div className="min-h-screen text-sand">
      <div className="mx-auto flex max-w-7xl flex-col gap-8 px-4 py-8 md:px-8">
        <header className="grid gap-6 lg:grid-cols-[1.3fr_0.7fr]">
          <div className="glass rounded-[2rem] border border-white/10 p-8 shadow-glow">
            <p className="mb-3 text-sm uppercase tracking-[0.35em] text-sky">AI customs workflow</p>
            <h1 className="max-w-3xl font-display text-4xl leading-tight text-white md:text-6xl">
              Customs clearance copilot for shipment document intake, risk scoring, and broker-ready actioning.
            </h1>
            <p className="mt-5 max-w-2xl text-base leading-7 text-sand/80">
              Upload invoices, packing lists, or bills of lading. The system extracts shipment data, screens it
              against customs constraints, and predicts clearance readiness in real time.
            </p>
          </div>

          <div className="glass rounded-[2rem] border border-white/10 p-6">
            <div className="mb-6 flex items-center justify-between">
              <span className="text-sm uppercase tracking-[0.3em] text-mint">System posture</span>
              <span className="rounded-full bg-mint/15 px-3 py-1 text-sm text-mint">Live</span>
            </div>
            <div className="grid gap-4">
              <MetricCard label="Automation Coverage" value="78%" />
              <MetricCard label="Avg Clearance Forecast" value={latest?.compliance.clearance_prediction ?? "Pending"} />
              <MetricCard label="Current Risk Band" value={latest?.compliance.risk_level ?? "Unknown"} />
            </div>
          </div>
        </header>

        <section className="grid gap-8 lg:grid-cols-[0.95fr_1.05fr]">
          <form onSubmit={onSubmit} className="glass rounded-[2rem] border border-white/10 p-6">
            <div className="mb-5">
              <p className="text-sm uppercase tracking-[0.3em] text-ember">Shipment intake</p>
              <h2 className="mt-2 font-display text-3xl text-white">Upload customs documents</h2>
            </div>

            <label
              htmlFor="file-upload"
              className="flex min-h-48 cursor-pointer flex-col items-center justify-center rounded-[1.5rem] border border-dashed border-sky/40 bg-white/5 px-6 text-center"
            >
              <span className="text-lg text-white">Drag files here or browse locally</span>
              <span className="mt-2 text-sm text-sand/70">Supports image files and text-based shipment documents.</span>
              <input
                id="file-upload"
                type="file"
                multiple
                aria-label="Upload shipment documents"
                className="hidden"
                onChange={(event) => setFiles(Array.from(event.target.files || []))}
              />
            </label>

            <div className="mt-5 flex flex-wrap gap-2">
              {files.map((file) => (
                <span key={file.name} className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-sm">
                  {file.name}
                </span>
              ))}
            </div>

            {error ? <p className="mt-4 text-sm text-red-300" role="alert">{error}</p> : null}

            <button
              type="submit"
              disabled={loading}
              className="mt-6 rounded-full bg-ember px-5 py-3 font-semibold text-ink-950 transition hover:brightness-110 disabled:opacity-60"
            >
              {loading ? "Analyzing..." : "Run AI clearance workflow"}
            </button>
          </form>

          <div className="glass rounded-[2rem] border border-white/10 p-6">
            <div className="mb-5">
              <p className="text-sm uppercase tracking-[0.3em] text-sky">Processing pipeline</p>
              <h2 className="mt-2 font-display text-3xl text-white">Real-time orchestration</h2>
            </div>
            <div className="space-y-4">
              {pipeline.map((stage) => (
                <div key={stage.name} className="rounded-2xl border border-white/10 bg-white/5 p-4">
                  <div className="flex items-center justify-between">
                    <span className="text-white">{stage.name}</span>
                    <span
                      className={`rounded-full px-3 py-1 text-xs uppercase tracking-[0.2em] ${
                        stage.status === "completed"
                          ? "bg-mint/15 text-mint"
                          : stage.status === "running"
                            ? "bg-sky/15 text-sky"
                            : "bg-white/10 text-sand/70"
                      }`}
                    >
                      {stage.status}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="grid gap-8 xl:grid-cols-[1.1fr_0.9fr]">
          <div className="space-y-8">
            <Panel
              eyebrow="Extracted shipment data"
              title={latest ? latest.filename : "Waiting for analysis"}
              body={
                latest ? (
                  <div className="grid gap-4 md:grid-cols-2">
                    <DataPoint label="Importer" value={latest.extracted_data.importer} />
                    <DataPoint label="Exporter" value={latest.extracted_data.exporter} />
                    <DataPoint label="Incoterm" value={latest.extracted_data.incoterm} />
                    <DataPoint label="Port of Entry" value={latest.extracted_data.port_of_entry} />
                    <DataPoint label="Document Class" value={latest.classification} />
                    <DataPoint label="Shipment Value" value={`$${latest.extracted_data.shipment_value_usd.toLocaleString()}`} />
                  </div>
                ) : (
                  <EmptyState text="Run an upload to populate extracted shipment fields." />
                )
              }
            />

            <Panel
              eyebrow="AI assistant"
              title="Broker guidance"
              body={
                latest ? (
                  <div className="space-y-4">
                    <p className="leading-7 text-sand/80">{latest.assistant_summary}</p>
                    <div className="grid gap-3">
                      {latest.compliance.suggestions.map((suggestion) => (
                        <div key={suggestion} className="rounded-2xl border border-mint/20 bg-mint/10 p-4 text-sm">
                          {suggestion}
                        </div>
                      ))}
                    </div>
                  </div>
                ) : (
                  <EmptyState text="The assistant will summarize key customs actions and blockers here." />
                )
              }
            />
          </div>

          <div className="space-y-8">
            <Panel
              eyebrow="Risk analysis"
              title="Compliance signal"
              body={
                latest ? (
                  <div className="space-y-5">
                    <div className="grid gap-4 sm:grid-cols-3">
                      <MetricCard label="Score" value={`${latest.compliance.score}/100`} />
                      <MetricCard label="Risk" value={latest.compliance.risk_level} />
                      <MetricCard label="Prediction" value={latest.compliance.clearance_prediction} />
                    </div>
                    <RiskMeter score={latest.compliance.score} />
                    <div className="space-y-3">
                      {latest.compliance.issues.length ? (
                        latest.compliance.issues.map((issue) => (
                          <div key={`${issue.title}-${issue.regulation}`} className="rounded-2xl border border-white/10 bg-white/5 p-4">
                            <div className="flex items-center justify-between gap-4">
                              <p className="font-semibold text-white">{issue.title}</p>
                              <span className="rounded-full bg-ember/15 px-3 py-1 text-xs uppercase tracking-[0.25em] text-ember">
                                {issue.severity}
                              </span>
                            </div>
                            <p className="mt-2 text-sm leading-6 text-sand/75">{issue.detail}</p>
                            <p className="mt-2 text-xs uppercase tracking-[0.2em] text-sky">{issue.regulation}</p>
                          </div>
                        ))
                      ) : (
                        <EmptyState text="No compliance issues found." />
                      )}
                    </div>
                  </div>
                ) : (
                  <EmptyState text="Risk scoring appears after the backend completes document analysis." />
                )
              }
            />
          </div>
        </section>
      </div>
    </div>
  );
}

function Panel({ eyebrow, title, body }) {
  return (
    <div className="glass rounded-[2rem] border border-white/10 p-6">
      <p className="text-sm uppercase tracking-[0.3em] text-sky">{eyebrow}</p>
      <h3 className="mt-2 font-display text-3xl text-white">{title}</h3>
      <div className="mt-5">{body}</div>
    </div>
  );
}

function MetricCard({ label, value }) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
      <p className="text-xs uppercase tracking-[0.3em] text-sand/60">{label}</p>
      <p className="mt-2 text-lg font-semibold text-white">{value}</p>
    </div>
  );
}

function DataPoint({ label, value }) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
      <p className="text-xs uppercase tracking-[0.3em] text-sand/60">{label}</p>
      <p className="mt-2 text-base text-white">{value}</p>
    </div>
  );
}

function EmptyState({ text }) {
  return <p className="rounded-2xl border border-dashed border-white/10 p-6 text-sand/70">{text}</p>;
}

function RiskMeter({ score }) {
  return (
    <div>
      <div className="mb-2 flex items-center justify-between text-sm text-sand/70">
        <span>Clearance confidence</span>
        <span>{score}%</span>
      </div>
      <div className="h-3 overflow-hidden rounded-full bg-white/10">
        <div className="h-full rounded-full bg-gradient-to-r from-ember via-sand to-mint" style={{ width: `${score}%` }} />
      </div>
    </div>
  );
}

export default function AppWithErrorBoundary() {
  return (
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  );
}
