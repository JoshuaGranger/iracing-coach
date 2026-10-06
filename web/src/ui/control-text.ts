import { h, options, type ComponentChildren, type VNode } from "preact";

/**
 * Control text sits on the control's centre line by its capital letters (round 5, U1; backlog #80).
 *
 * A line box centres a font's ascent and descent, not its capitals, so label text in a control rides a pixel or two high or low
 * and differs between the display and the body face. `text-box: trim-both cap alphabetic` (styles.css, `.ctl-t`) trims a text
 * run's box to its capitals, whatever the font and size, and the control's own `align-items: center` then centres the capitals.
 * It applies to block containers and inline boxes, not to the anonymous box browsers put around bare text in a flex container, so
 * the text of every control is given a span of its own: this wraps the bare text children of a control's element in
 * `<span class="ctl-t">` as the element is created. Neighbouring text pieces become one span (`x{n}` is one run, not three flex
 * items). Elements with markup of their own for the text (a span, a label) are left to the stylesheet's leaf-span rule.
 */

/** The class tokens that make an element a control: a chip, pill, badge, tab, segment, toggle, tag or button. */
const CONTROL_TOKENS = /(^|[\s-])(btn|button|chip|badge|pill|tab|toggle|seg|tag)($|[\s-])/;
/** A class that names the text inside a control (`ri-btn-text`, `ms-pill-text`, `ri-pill-label`): a carrier of the text, not a control. */
const TEXT_CARRIER = /(^|[\s-])(text|label)($|[\s-])/;
const CONTROL_TAGS = new Set(["button", "summary"]);
const CONTROL_ROLES = new Set(["tab", "button"]);

export function isControl(type: unknown, props: { class?: unknown; className?: unknown; role?: unknown }): boolean {
  if (typeof type !== "string") return false;
  if (CONTROL_TAGS.has(type)) return true;
  if (typeof props.role === "string" && CONTROL_ROLES.has(props.role)) return true;
  const cls = typeof props.class === "string" ? props.class : typeof props.className === "string" ? props.className : "";
  return cls !== "" && CONTROL_TOKENS.test(cls) && !TEXT_CARRIER.test(cls);
}

const isText = (child: unknown): child is string | number => typeof child === "string" || typeof child === "number";

/** The children with each run of adjacent bare text (strings and numbers) in one `ctl-t` span; the original array when it has none. */
export function wrapControlText(children: ComponentChildren): ComponentChildren {
  if (isText(children)) return String(children).trim() === "" ? children : h("span", { class: "ctl-t" }, children);
  if (!Array.isArray(children)) return children;
  if (!children.some(isText)) return children;
  const out: ComponentChildren[] = [];
  let run: (string | number)[] = [];
  const flush = (): void => {
    if (run.length === 0) return;
    if (run.join("").trim() === "") out.push(...run);
    else out.push(h("span", { class: "ctl-t" }, ...run));
    run = [];
  };
  for (const child of children) {
    if (isText(child)) run.push(child);
    else {
      flush();
      out.push(child as ComponentChildren);
    }
  }
  flush();
  return out;
}

let installed = false;

/** Hook the creation of elements so every control's bare text is wrapped. Safe to call twice. */
export function installControlText(): void {
  if (installed) return;
  installed = true;
  const previous = options.vnode;
  options.vnode = (vnode: VNode) => {
    const props = vnode.props as { class?: unknown; className?: unknown; role?: unknown; children?: ComponentChildren; dangerouslySetInnerHTML?: unknown };
    if (props && props.dangerouslySetInnerHTML === undefined && props.children != null && isControl(vnode.type, props)) props.children = wrapControlText(props.children);
    previous?.(vnode);
  };
}
