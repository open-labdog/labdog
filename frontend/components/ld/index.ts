/**
 * LabDog UI kit for screens on the rail-and-pane shell.
 *
 * Dense, token-driven primitives ported from the design prototype. They
 * read the theme tokens in globals.css directly, so they render in both
 * themes without variants. Every screen uses these; forms are plain
 * controls with the `.inp` class inside a `Field`, buttons are the `.btn`
 * classes, and a modal is `Modal` (or `Confirm` for a yes/no).
 */
export { Dot, Status, RunStatus, Tag, Provenance, Kbd, Empty } from "./atoms"
export { Panel, Split, PageHead, Tabs, Seg, type Crumb, type TabDef, type SegOption } from "./layout"
export { Filter, type FilterOption } from "./filter"
export { Table, type Col, type Sort } from "./table"
export { Spark, Meter, StatusBar } from "./charts"
export { Modal } from "./modal"
export { Confirm, type ConfirmProps } from "./confirm"
export { Field, Help } from "./form"
export { Banner, Toolbar, BulkBar } from "./banner"
export { Facts, Stat, CodeBlock, Copy, type Fact } from "./facts"
export { Steps } from "./steps"
export { toneVar, toneSoft, toneInk, tint, type Tone } from "./tone"
