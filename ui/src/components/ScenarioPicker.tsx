import type { Scenario } from "../fixtures";

// Every screen is driven entirely by a named fixture scenario — switching this
// select swaps which fixture file is rendered. There is no live data anywhere
// in this build (T-183); see the fixture-mode badge in the header.
export function ScenarioPicker<T>({
  scenarios,
  value,
  onChange,
  label = "Fixture scenario",
}: {
  scenarios: Scenario<T>[];
  value: string;
  onChange: (key: string) => void;
  label?: string;
}) {
  const id = `scenario-${label.replace(/\s+/g, "-").toLowerCase()}`;
  return (
    <div className="scenario-picker">
      <label htmlFor={id}>{label}</label>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
        {scenarios.map((s) => (
          <option key={s.key} value={s.key}>
            {s.label}
          </option>
        ))}
      </select>
    </div>
  );
}
