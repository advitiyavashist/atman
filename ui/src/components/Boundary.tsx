/**
 * One pane failing must not take the app with it.
 *
 * This exists because it happened: the board snapshot records a run's verdict
 * as `{kind, sha}` where the contract's other routes use a string, React
 * refused to render the object, and the whole page went blank — a tester would
 * have seen nothing at all and had no way to tell that from an empty board.
 *
 * A blank screen is the least honest thing this app could do, so each column
 * is wrapped: the pane that failed says so, names the error, and the rest of
 * the screen keeps working.
 */

import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  what: string;
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class Boundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // eslint-disable-next-line no-console
    console.error(`[atman] ${this.props.what} failed to render`, error, info.componentStack);
  }

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <section className="pane" aria-label={`${this.props.what} failed`}>
        <p className="failure" role="alert">
          <strong>{this.props.what} could not be drawn.</strong> {error.message}
        </p>
        <p className="muted">
          This is a fault in the app, not a statement about the board: the data may be fine. The other panes are
          unaffected. Reload after the fix, or read the same records with <code>atm</code>.
        </p>
        <button type="button" className="btn-quiet" onClick={() => this.setState({ error: null })}>
          try drawing it again
        </button>
      </section>
    );
  }
}
