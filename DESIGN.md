---
name: Clean My Codex
description: A compact operations console for safely maintaining Codex workspace data.
colors:
  canvas-black: "#000000"
  panel-black: "#0a0a0a"
  raised-charcoal: "#101010"
  hover-charcoal: "#151515"
  divider-gray: "#242424"
  control-gray: "#3a3a3a"
  primary-text: "#ededed"
  secondary-text: "#9b9b9b"
  disabled-text: "#666666"
  healthy-green: "#46a758"
  review-amber: "#d9a441"
  information-blue: "#539bf5"
  destructive-red: "#e5484d"
typography:
  headline:
    fontFamily: "Geist, -apple-system, BlinkMacSystemFont, SF Pro Text, Segoe UI, sans-serif"
    fontSize: "20px"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "normal"
  title:
    fontFamily: "Geist, -apple-system, BlinkMacSystemFont, SF Pro Text, Segoe UI, sans-serif"
    fontSize: "14px"
    fontWeight: 600
    lineHeight: 1.25
    letterSpacing: "normal"
  body:
    fontFamily: "Geist, -apple-system, BlinkMacSystemFont, SF Pro Text, Segoe UI, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.45
    letterSpacing: "normal"
  label:
    fontFamily: "Geist, -apple-system, BlinkMacSystemFont, SF Pro Text, Segoe UI, sans-serif"
    fontSize: "11px"
    fontWeight: 500
    lineHeight: 1.4
    letterSpacing: "normal"
  code:
    fontFamily: "Geist Mono, SFMono-Regular, Consolas, Liberation Mono, monospace"
    fontSize: "11px"
    fontWeight: 400
    lineHeight: 1.45
    letterSpacing: "normal"
rounded:
  mark: "5px"
  control: "6px"
  dialog: "8px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
components:
  button-primary:
    backgroundColor: "#ffffff"
    textColor: "{colors.canvas-black}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "7px 11px"
    height: "34px"
  button-secondary:
    backgroundColor: "{colors.panel-black}"
    textColor: "{colors.primary-text}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "7px 11px"
    height: "34px"
  button-danger-quiet:
    backgroundColor: "transparent"
    textColor: "#ff8589"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "7px 11px"
    height: "34px"
  input:
    backgroundColor: "#050505"
    textColor: "{colors.primary-text}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "7px 10px"
    height: "36px"
---

# Design System: Clean My Codex

## Overview

**Creative North Star: "The Codex Operations Console"**

The interface behaves like a precise maintenance workspace: compact, calm, and evidence-led. Near-black surfaces and neutral dividers keep attention on the data, while a master-detail structure separates fast scanning from technical inspection.

Safety is expressed through workflow rather than warning density. Each item carries one primary status, actions appear only when relevant, and destructive red is delayed until an advanced or confirmed destructive context.

**Key Characteristics:**
- Compact developer-tool density with restrained black, white, and neutral-gray surfaces.
- One status per row, supported by a semantic mini icon and readable text.
- Master-detail workspaces with technical evidence collapsed by default.
- White primary commands and progressively disclosed destructive actions.
- Workspace paths use monospace only where their data structure benefits from it.

## Colors

The palette is neutral-first; semantic colors are small signals rather than surface themes.

### Primary
- **Canvas Black** (`#000000`): The application and sidebar ground.
- **Primary Text** (`#ededed`): Main labels, titles, and readable content.

### Secondary
- **Healthy Green** (`#46a758`): Ready, available, and verified states.
- **Review Amber** (`#d9a441`): Missing paths and cleanup states requiring attention.
- **Information Blue** (`#539bf5`): Preview, informational, and manual-inspection states.
- **Destructive Red** (`#e5484d`): Confirmation-layer destructive actions only.

### Neutral
- **Panel Black** (`#0a0a0a`): Controls and bounded work surfaces.
- **Raised Charcoal** (`#101010`): Selection bars and restrained raised states.
- **Hover Charcoal** (`#151515`): Focused and hovered rows.
- **Divider Gray** (`#242424`): Primary separators.
- **Control Gray** (`#3a3a3a`): Interactive borders.
- **Secondary Text** (`#9b9b9b`): Supporting descriptions and metadata.
- **Disabled Text** (`#666666`): Disabled controls and unavailable data.

**The Signal Rarity Rule.** Semantic color stays small and contained. It may identify status or confirmation, but it does not fill routine page actions.

## Typography

**Display Font:** Geist with macOS and system UI fallbacks  
**Body Font:** Geist with macOS and system UI fallbacks  
**Label/Mono Font:** Geist Mono with SFMono and Consolas fallbacks

**Character:** A compact sans hierarchy keeps operational data legible without turning headings into promotional display text. Monospace is reserved for paths, identifiers, and technical records.

### Hierarchy
- **Headline** (600, 20px, 1.2): Page titles only.
- **Title** (600, 14px, 1.25): Inspector, section, and list group headings.
- **Body** (400, 13px, 1.45): General interface content.
- **Label** (500, 11px, 1.4): Metadata, filters, statuses, and support text.
- **Code** (400, 11px, 1.45): Paths, thread IDs, and raw reference data.

**The Operational Scale Rule.** Interior controls and panels remain compact; no page uses hero-scale typography.

## Layout

Desktop uses a 224px navigation rail and a fluid content canvas. Primary pages use a master-detail grid with a flexible list and a 320–360px inspector. Filters stay in one compact row, tables use stable fixed columns, and technical data is moved into disclosure sections rather than widening rows.

At 980px the inspector moves below the list. At 800px navigation becomes a horizontally scrollable top rail. At 640px toolbars reflow into one or two columns, controls reach 42px touch height, and fixed-format tables scroll horizontally inside their own frame without widening the page.

Spacing follows a tight 4/8/12/16/24px rhythm. Related controls stay close; workspace boundaries and inspectors receive the larger steps.

## Elevation & Depth

The system is flat by default. Depth comes from tonal layering and 1px borders, not ambient card shadows. A wide shadow is used only for protected-focus surfaces such as confirmation dialogs and transient toasts.

**The Structural Depth Rule.** Use borders and tonal steps for permanent layout; reserve shadows for interruption and transient feedback.

## Shapes

Controls, framed tools, and repeated items use 6px corners. The brand mark uses a compact rounded-square frame and confirmation dialogs use 8px. Tiny badges may be fully rounded because their shape communicates a compact label, not a container.

## Components

### Buttons
- **Shape:** Compact 6px corners and a 34px desktop height.
- **Primary:** White fill, black text, and 600 weight for the safest recommended next action.
- **Secondary:** Near-black fill with a neutral control border.
- **Danger Quiet:** Transparent surface and restrained red text outside a confirmation dialog.
- **Hover / Focus:** Tonal hover shift; focus uses a blue border and a visible two-ring outline.

### Status
- **Style:** One 13px semantic line icon followed by one plain-language state. Check, warning, information, error, idle, and sync states use distinct geometry.
- **State:** Green means connected or verified, amber means attention, blue means information, and red means a blocking/manual review state. Shape always carries the meaning before color.

### Identity
- **Mark:** A four-part workspace grid with the final cell expressed as a return path, representing organized records and reversible cleanup.
- **Credit:** Version and creator attribution stay in the navigation footer so identity remains visible without competing with the active workflow.
- **Scale:** The SVG mark must remain legible at favicon, sidebar, and high-density display sizes.

### Skeleton Scan
- **Shape:** Loading placeholders preserve the exact table, list, and inspector geometry of the arriving content.
- **Motion:** A solid neutral sweep travels through each placeholder; no decorative gradient or continuous page motion is used.
- **Feedback:** Scan controls rotate their refresh icon, and the top status changes from `Scanning Codex` to `Connected to Codex`.
- **Accessibility:** Skeletons are hidden from assistive technology, containers use `aria-busy`, and reduced-motion preferences disable the sweep.

### Containers
- **Corner Style:** 6px for bounded tools and list frames.
- **Background:** Canvas black, panel black, and raised charcoal.
- **Shadow Strategy:** None at rest.
- **Border:** One-pixel neutral dividers establish structure.

### Inputs / Fields
- **Style:** Near-black fill, 1px control-gray border, 6px corners, and compact labels.
- **Focus:** Blue border with a two-ring focus outline.
- **Disabled:** Dimmed text and border without changing layout dimensions.

### Navigation
- **Style:** Monochrome line icons, compact text, transparent default state, and a quiet raised active row.
- **Responsive:** The rail becomes a horizontally scrollable tab strip without hiding destinations.
- **Motion:** Each destination has one one-second ease-in-out click sequence tied to its meaning: chat bubbles pop inward, search scans a drawn folder, relocation arrows enter from opposite directions, the cleaner brush sweeps, the Trash lid opens and settles, and log lines draw in sequence.

### Selection Controls
- **Shape:** Checkboxes use a stable 17px circle with a neutral resting border.
- **Feedback:** Selection compresses and rebounds once while the check draws in with eased overshoot; indeterminate selection uses a short horizontal line.
- **Performance:** Navigation motion is event-driven, affects only the clicked 16px SVG, and prefers transforms and opacity. Stroke drawing is limited to the few paths whose meaning depends on drawing.
- **Accessibility:** Labels and native checked/indeterminate state remain intact, a selected check remains visible on hover, keyboard focus stays visible, and reduced-motion preferences collapse animation durations.

### Contextual Inspector
- **Style:** A border-separated detail column with one status, a compact definition list, and collapsed technical references.
- **Behavior:** Selection changes context without navigating away; destructive actions stay under advanced disclosure or typed confirmation.

## Do's and Don'ts

### Do:
- **Do** show exactly one primary health state for each list row.
- **Do** keep technical references accessible inside a collapsed inspector section.
- **Do** shorten home-directory paths to `~` in the interface.
- **Do** use one obvious next action per row and move multi-step decisions into the inspector.
- **Do** keep destructive actions reversible through Trash Bin and typed confirmation.

### Don't:
- **Don't** stack status chips, database flags, and source labels in a table cell.
- **Don't** repeat red cleanup buttons on every row.
- **Don't** expose raw JSON as the default page content.
- **Don't** use gradients, glow, glass, oversized headings, or promotional dashboard cards.
- **Don't** use monospace for ordinary prose or navigation.
