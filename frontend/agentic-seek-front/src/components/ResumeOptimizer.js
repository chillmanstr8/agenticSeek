import React, { useState, useRef } from "react";
import ReactMarkdown from "react-markdown";
import axios from "axios";
import "./ResumeOptimizer.css";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

function KeywordChip({ label }) {
  return <span className="ro-keyword-chip">{label}</span>;
}

function ResumeOptimizer() {
  // Job description input
  const [jdInputMode, setJdInputMode] = useState("text"); // "url" | "text"
  const [jobUrl, setJobUrl] = useState("");
  const [jobText, setJobText] = useState("");

  // Master resume input
  const [masterResumeMode, setMasterResumeMode] = useState("text"); // "file" | "text"
  const [masterResumeText, setMasterResumeText] = useState("");
  const [masterResumeFileName, setMasterResumeFileName] = useState("");
  const fileInputRef = useRef(null);

  // Analysis results
  const [analysis, setAnalysis] = useState(null);
  const [analysisExpanded, setAnalysisExpanded] = useState(true);

  // Generated resume
  const [generatedResume, setGeneratedResume] = useState(null);

  // UI state
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [isGenerating, setIsGenerating] = useState(false);
  const [error, setError] = useState(null);

  // -------------------------------------------------------------------------
  // Helpers
  // -------------------------------------------------------------------------

  const buildJobPayload = () => {
    if (jdInputMode === "url") {
      return { job_url: jobUrl.trim(), job_text: null };
    }
    return { job_url: null, job_text: jobText.trim() };
  };

  const hasJobInput = () => {
    return jdInputMode === "url" ? jobUrl.trim() !== "" : jobText.trim() !== "";
  };

  const downloadResume = () => {
    if (!generatedResume?.tailored_resume) return;
    const blob = new Blob([generatedResume.tailored_resume], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "tailored_resume.md";
    a.click();
    URL.revokeObjectURL(url);
  };

  // -------------------------------------------------------------------------
  // PDF upload
  // -------------------------------------------------------------------------

  const handleFileUpload = async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    if (file.name.toLowerCase().endsWith(".pdf")) {
      // Extract text server-side
      const formData = new FormData();
      formData.append("file", file);
      try {
        const res = await axios.post(`${BACKEND_URL}/resume/upload_pdf`, formData, {
          headers: { "Content-Type": "multipart/form-data" },
        });
        setMasterResumeText(res.data.text);
        setMasterResumeFileName(file.name);
      } catch (err) {
        setError(
          err.response?.data?.error || "Failed to extract text from PDF."
        );
      }
    } else {
      // Plain text / markdown — read in browser
      const reader = new FileReader();
      reader.onload = (ev) => {
        setMasterResumeText(ev.target.result);
        setMasterResumeFileName(file.name);
      };
      reader.readAsText(file);
    }
  };

  // -------------------------------------------------------------------------
  // Analyze
  // -------------------------------------------------------------------------

  const handleAnalyze = async () => {
    setError(null);
    setAnalysis(null);
    if (!hasJobInput()) {
      setError(
        jdInputMode === "url"
          ? "Please enter a job posting URL."
          : "Please paste the job description text."
      );
      return;
    }

    setIsAnalyzing(true);
    try {
      const res = await axios.post(`${BACKEND_URL}/resume/analyze`, buildJobPayload());
      setAnalysis(res.data);
      setAnalysisExpanded(true);
    } catch (err) {
      setError(
        err.response?.data?.error || err.response?.data?.analysis_summary || "Failed to analyze job description."
      );
    } finally {
      setIsAnalyzing(false);
    }
  };

  // -------------------------------------------------------------------------
  // Generate
  // -------------------------------------------------------------------------

  const handleGenerate = async () => {
    setError(null);
    setGeneratedResume(null);
    if (!hasJobInput()) {
      setError("Please provide the job description (URL or text).");
      return;
    }
    if (!masterResumeText.trim()) {
      setError("Please provide your master resume (upload a file or paste the text).");
      return;
    }

    setIsGenerating(true);
    try {
      const payload = {
        ...buildJobPayload(),
        master_resume: masterResumeText.trim(),
      };
      const res = await axios.post(`${BACKEND_URL}/resume/generate`, payload);
      setGeneratedResume(res.data);
    } catch (err) {
      setError(
        err.response?.data?.error || "Failed to generate tailored resume."
      );
    } finally {
      setIsGenerating(false);
    }
  };

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  return (
    <div className="ro-container">
      {/* ---- Header ---- */}
      <div className="ro-header">
        <h2 className="ro-title">Resume Optimizer</h2>
        <p className="ro-subtitle">
          Paste a job description URL or text, upload your master resume, and get an
          ATS-optimised tailored resume with keyword analysis.
        </p>
      </div>

      {/* ---- Error banner ---- */}
      {error && (
        <div className="ro-error-banner">
          <span>{error}</span>
          <button className="ro-error-close" onClick={() => setError(null)}>✕</button>
        </div>
      )}

      {/* ---- Input panels ---- */}
      <div className="ro-input-panels">

        {/* Left: Job Description */}
        <div className="ro-panel">
          <h3 className="ro-panel-title">Job Description</h3>
          <div className="ro-tab-bar">
            <button
              className={`ro-tab ${jdInputMode === "text" ? "ro-tab-active" : ""}`}
              onClick={() => setJdInputMode("text")}
            >
              Paste Text
            </button>
            <button
              className={`ro-tab ${jdInputMode === "url" ? "ro-tab-active" : ""}`}
              onClick={() => setJdInputMode("url")}
            >
              Paste URL
            </button>
          </div>

          {jdInputMode === "url" ? (
            <input
              type="url"
              className="ro-text-input ro-url-input"
              placeholder="https://company.com/careers/job-posting"
              value={jobUrl}
              onChange={(e) => setJobUrl(e.target.value)}
            />
          ) : (
            <textarea
              className="ro-textarea"
              placeholder="Paste the full job description here…"
              value={jobText}
              onChange={(e) => setJobText(e.target.value)}
            />
          )}

          <div className="ro-panel-actions">
            <button
              className="ro-btn ro-btn-secondary"
              onClick={handleAnalyze}
              disabled={isAnalyzing || isGenerating}
            >
              {isAnalyzing ? "Analyzing…" : "Analyze Job"}
            </button>
          </div>
        </div>

        {/* Right: Master Resume */}
        <div className="ro-panel">
          <h3 className="ro-panel-title">Master Resume</h3>
          <div className="ro-tab-bar">
            <button
              className={`ro-tab ${masterResumeMode === "text" ? "ro-tab-active" : ""}`}
              onClick={() => setMasterResumeMode("text")}
            >
              Paste Text
            </button>
            <button
              className={`ro-tab ${masterResumeMode === "file" ? "ro-tab-active" : ""}`}
              onClick={() => setMasterResumeMode("file")}
            >
              Upload File
            </button>
          </div>

          {masterResumeMode === "file" ? (
            <div className="ro-file-drop-area" onClick={() => fileInputRef.current?.click()}>
              <input
                type="file"
                ref={fileInputRef}
                style={{ display: "none" }}
                accept=".txt,.md,.pdf"
                onChange={handleFileUpload}
              />
              {masterResumeFileName ? (
                <p className="ro-file-name">📄 {masterResumeFileName}</p>
              ) : (
                <p className="ro-file-placeholder">
                  Click to upload <strong>.txt</strong>, <strong>.md</strong>, or <strong>.pdf</strong>
                </p>
              )}
            </div>
          ) : (
            <textarea
              className="ro-textarea"
              placeholder="Paste your master resume text here…"
              value={masterResumeText}
              onChange={(e) => setMasterResumeText(e.target.value)}
            />
          )}

          <div className="ro-panel-actions">
            <button
              className="ro-btn ro-btn-primary"
              onClick={handleGenerate}
              disabled={isAnalyzing || isGenerating}
            >
              {isGenerating ? "Generating…" : "Generate Resume"}
            </button>
          </div>
        </div>
      </div>

      {/* ---- Analysis Results ---- */}
      {analysis && (
        <div className="ro-section">
          <div
            className="ro-section-header"
            onClick={() => setAnalysisExpanded((v) => !v)}
          >
            <h3>Job Analysis</h3>
            <span className="ro-chevron">{analysisExpanded ? "▼" : "▶"}</span>
          </div>

          {analysisExpanded && (
            <div className="ro-analysis-body">
              {analysis.analysis_summary && (
                <p className="ro-analysis-summary">{analysis.analysis_summary}</p>
              )}

              <div className="ro-analysis-grid">
                {analysis.keywords?.length > 0 && (
                  <div className="ro-analysis-group">
                    <h4>ATS Keywords</h4>
                    <div className="ro-chips">
                      {analysis.keywords.map((k, i) => (
                        <KeywordChip key={i} label={k} />
                      ))}
                    </div>
                  </div>
                )}

                {analysis.required_skills?.length > 0 && (
                  <div className="ro-analysis-group">
                    <h4>Required Skills</h4>
                    <div className="ro-chips">
                      {analysis.required_skills.map((s, i) => (
                        <KeywordChip key={i} label={s} />
                      ))}
                    </div>
                  </div>
                )}

                {analysis.implied_skills?.length > 0 && (
                  <div className="ro-analysis-group">
                    <h4>Implied / Soft Skills</h4>
                    <div className="ro-chips ro-chips-soft">
                      {analysis.implied_skills.map((s, i) => (
                        <KeywordChip key={i} label={s} />
                      ))}
                    </div>
                  </div>
                )}

                {analysis.culture_signals?.length > 0 && (
                  <div className="ro-analysis-group">
                    <h4>Culture Signals</h4>
                    <div className="ro-chips ro-chips-culture">
                      {analysis.culture_signals.map((s, i) => (
                        <KeywordChip key={i} label={s} />
                      ))}
                    </div>
                  </div>
                )}

                {analysis.top_priorities?.length > 0 && (
                  <div className="ro-analysis-group ro-analysis-group-wide">
                    <h4>Top Priorities for This Role</h4>
                    <ol className="ro-priorities-list">
                      {analysis.top_priorities.map((p, i) => (
                        <li key={i}>{p}</li>
                      ))}
                    </ol>
                  </div>
                )}

                {analysis.seniority_level && analysis.seniority_level !== "unknown" && (
                  <div className="ro-analysis-group">
                    <h4>Seniority Level</h4>
                    <span className="ro-seniority-badge">{analysis.seniority_level}</span>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ---- Generated Resume ---- */}
      {generatedResume && (
        <div className="ro-section">
          <div className="ro-section-header">
            <h3>Tailored Resume</h3>
            <div className="ro-resume-meta">
              {generatedResume.ats_score_estimate && generatedResume.ats_score_estimate !== "unknown" && (
                <span className="ro-ats-badge">
                  ATS Match: {generatedResume.ats_score_estimate}
                </span>
              )}
              <button className="ro-btn ro-btn-download" onClick={downloadResume}>
                ⬇ Download .md
              </button>
            </div>
          </div>

          {generatedResume.missing_from_master?.length > 0 && (
            <div className="ro-gaps-section">
              <h4>⚠ Gaps to Address</h4>
              <p className="ro-gaps-hint">
                The following skills/requirements from the job description are not reflected in your master resume.
                Consider adding them if you have relevant experience, or be prepared to address them in an interview.
              </p>
              <ul className="ro-gaps-list">
                {generatedResume.missing_from_master.map((g, i) => (
                  <li key={i}>{g}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="ro-resume-preview">
            <ReactMarkdown>{generatedResume.tailored_resume}</ReactMarkdown>
          </div>
        </div>
      )}
    </div>
  );
}

export default ResumeOptimizer;
