import { render } from "preact";

import { App } from "./app";
import "./styles.css";
import { installControlBehaviour } from "./ui/control-behaviour";
import { installControlText } from "./ui/control-text";

installControlText();
installControlBehaviour();
render(<App />, document.getElementById("app")!);
