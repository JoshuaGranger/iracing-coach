import { render } from "preact";

import { App } from "./app";
import { initTextSize } from "./core/text-size";
import "./styles.css";

// The text size this device last showed is applied before the first render; the driver's saved step takes over when prefs load.
initTextSize();
render(<App />, document.getElementById("app")!);
