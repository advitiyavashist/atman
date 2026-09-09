import { useState } from "react";
import { DAY_ONE_STEPS, dayOneHonesty, dayOneIntervene, dayOneLede } from "../copy";

function CopyCommand({ cmd }: { cmd: string }) {
  const [label, setLabel] = useState("Copy command");
  return (
    <button
      type="button"
      className="copy-run"
      onClick={() => {
        void navigator.clipboard?.writeText(cmd).then(() => {
          setLabel("Copied");
          window.setTimeout(() => setLabel("Copy command"), 1200);
        });
      }}
    >
      {label}
    </button>
  );
}

/** Day-one empty board: Objective · Team · Work, then Intervene. */
export function DayOnePath() {
  return (
    <div className="day-one" data-testid="day-one-path">
      <p className="empty-kicker">Day one</p>
      <p>
        <strong>Three steps.</strong> {dayOneLede}
      </p>
      <ol className="empty-steps">
        {DAY_ONE_STEPS.map((step, i) => (
          <li key={step.key}>
            <span className="n">{i + 1}</span>
            <div>
              <strong>{step.title}</strong> — {step.body}
              <div className="cta">{step.cmd}</div>
              <div className="empty-acts">
                <CopyCommand cmd={step.cmd} />
              </div>
            </div>
          </li>
        ))}
      </ol>
      <p className="empty-honesty">{dayOneHonesty}</p>
      <p className="empty-intervene">{dayOneIntervene}</p>
    </div>
  );
}
