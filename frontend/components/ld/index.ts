/**
 * LabDog UI kit for screens on the rail-and-pane shell.
 *
 * Dense, token-driven primitives ported from the design prototype. They
 * read the theme tokens in globals.css directly, so they render in both
 * themes without variants. Legacy pages keep using components/ui; new
 * screens use these.
 */
export { Dot, Status, Tag, Provenance, Kbd, Empty } from "./atoms"
export { Panel, Split, PageHead, Tabs, Seg, type Crumb, type TabDef, type SegOption } from "./layout"
export { Filter, type FilterOption } from "./filter"
export { Table, type Col, type Sort } from "./table"
export { Spark, Meter, StatusBar } from "./charts"
export { Modal } from "./modal"
export { toneVar, toneSoft, toneInk, tint, type Tone } from "./tone"
