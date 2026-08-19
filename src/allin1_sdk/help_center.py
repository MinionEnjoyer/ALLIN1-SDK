"""Searchable, task-oriented help for the ALLIN1 desktop application."""

from __future__ import annotations

from dataclasses import dataclass
import tkinter as tk
from tkinter import ttk


@dataclass(frozen=True)
class HelpTopic:
    """One concise help-center article."""

    key: str
    category: str
    title: str
    summary: str
    body: str
    keywords: tuple[str, ...] = ()


HELP_TOPICS: tuple[HelpTopic, ...] = (
    HelpTopic(
        "getting-started", "Start here", "Getting started",
        "Import content, resolve its integration graph, and export a reviewable report.",
        """1. Use Import content to open an addon.json or scan a DLC folder/archive.
2. Select a package and inspect every node in the Integration graph.
3. Resolve missing fields, source files, references, edition tags, and rollback steps.
4. Export the link report and keep it with the package's release artifacts.

The SDK audits and authors content. Its RPF workspace applies reviewed file and directory-tree plans to an exact mods copy or an explicitly isolated external workspace, including recursively nested entries up to eight archive levels. Full package installation, repair, and game launch remain the responsibility of the separate ALLIN1 Launcher.""",
        ("first run", "import", "addon", "linker", "report"),
    ),
    HelpTopic(
        "editions", "Start here", "Legacy and Enhanced installations",
        "Select the matching game build whenever native formats or encryption keys matter.",
        """Legacy and Enhanced can coexist on one PC. The RPF Explorer asks for the matching installation so it can use the correct archive keys and resource decoder.

Package scans label declared and inferred compatibility but never silently convert assets between editions. Treat an unresolved edition as a release blocker until it is tested against a specific build.""",
        ("gen9", "path", "target", "compatibility"),
    ),
    HelpTopic(
        "install-repair", "Environment", "SDK toolchain setup",
        "Prepare Python, CodeWalker, and the RPF helper.",
        """Install the Python package in an isolated environment, initialize the pinned CodeWalker submodule, and run runtools.ps1 to publish RpfPatcher.

The native viewers remain useful without the helper for lightweight header inspection. Recursive RPF indexing and structured Rockstar-resource XML require the helper. The SDK does not install or repair the gameplay client.""",
        ("python", "codewalker", "rpfpatcher", "dependencies", "build"),
    ),
    HelpTopic(
        "gameplay", "Automation", "Command-line workflows",
        "Run validation and inspection in scripts or continuous integration.",
        """The allin1-sdk command exposes package import, manifest validation, link reports, OIV plans, DLC inventory, vehicle compilation, and RPF indexing, exact-entry extraction, verified directory-subtree export, and archive diffing.

Commands return non-zero exit codes for invalid or unsafe inputs. Creating a single or multi-entry change plan never authorizes a write. plan-rpf-batch accepts a JSON changes list with add, replace, delete, mkdir, rmdir, rename, or upsert actions and produces one guarded plan for up to 1,000 root or deeply nested operations. plan-rpf-sync converts edits in a verified subtree workspace—including folder changes—into that same atomic format. Applying or rolling back a ready plan is a separate command and requires --acknowledge-write, a closed game, an authorized target, and unchanged input hashes. Real-archive canary mode proves replace/add/delete and rollback on a disposable copy.""",
        ("cli", "automation", "continuous integration", "script", "exit code"),
    ),
    HelpTopic(
        "console", "Automation", "SDK Console",
        "Use the complete command surface without leaving the desktop SDK.",
        """Use the prompt docked beneath every workspace, or press Ctrl+` to focus and expand it. Start typing to progressively filter commands. Suggestions include command syntax, options, local paths, and persistent command history.

The prompt stays docked at the bottom of every workspace. Ctrl+backtick focuses and expands it; Expand reveals output, history, and the complete suggestion table without leaving the current tool. Tab accepts the selected suggestion. Up and Down move through visible matches; Ctrl+Up and Ctrl+Down move through history. Enter runs the command asynchronously, Ctrl+L clears output, and Escape clears the command or collapses the dock.

The console invokes the same Click commands and safety checks as allin1-sdk in a terminal. It does not bypass target authorization, acknowledgements, hashes, locks, game-process checks, or rollback requirements. Type help for the catalog or help <command> for detailed syntax.""",
        ("console", "autocomplete", "completion", "history", "source", "terminal"),
    ),
    HelpTopic(
        "agent-api", "Automation", "AI agent integration",
        "Let local AI and developer tools inspect and operate the SDK through structured JSON.",
        """Run allin1-sdk agent-api and exchange one JSON object per line over standard input and output. Use the ping action to negotiate the protocol, catalog to discover command schemas and risk levels, and execute with a command plus a string args array.

The transport never invokes a shell and cannot evaluate Python. Requests are written to the per-user agent-api-audit.jsonl log. Game/archive mutation commands are rejected by default. A user must explicitly start the process with --allow-game-writes, and the requested SDK command must still pass its normal acknowledgement, closed-game, authorized-target, checksum, lock, backup, and rollback checks. list-installed-packages discovers receipt-backed installs; install-package validates and installs a mod.toml package; uninstall-package removes one through its owned-file and backup receipt.

This API is the stable foundation for AI-assisted package inspection and approved package lifecycle actions. The ALLIN1 Launcher remains the user-facing owner of interactive installation and game launch.""",
        ("ai", "agent", "api", "json", "jsonl", "stdio", "automation", "audit"),
    ),
    HelpTopic(
        "input", "Interface", "Navigating the SDK",
        "Use the integration graph, field inspector, menus, and search efficiently.",
        """Content imports are grouped under Import content. Review actions apply to the selected package, and Package intelligence contains cross-package tools.

Use the persistent sidebar to move between Integration, Native Assets, RPF Explorer, and Help Center. Ctrl+1–4 selects those workspaces without opening another application window. The SDK Console remains available along the bottom in all four contexts.

Select an integration node to see its source, contract, and linked fields. Select a field for a plain-language explanation. Ctrl+` focuses or expands the console dock and F1 routes to contextual help.""",
        ("navigation", "menus", "graph", "field", "keyboard"),
    ),
    HelpTopic(
        "packages", "Content", "Package auditing",
        "Inventory and classify content without executing compiled payloads.",
        """Import a loose DLC folder or supported OIV/ZIP/RAR/7z archive. The scanner inventories metadata, native assets, plug-in headers, dependencies, edition hints, and inferred destinations.

Generated addon.json files are drafts. Resolve their warnings and linker errors before packaging. DLL and ASI payloads are inspected as inert bytes and are never loaded by the SDK.""",
        ("mods", "package", "manifest", "archive", "audit", "plugin"),
    ),
    HelpTopic(
        "sdk", "Authoring", "Integration Linker",
        "Trace game-facing fields and audit add-on integration before installation.",
        """The Add-on SDK links authored package fields to metadata, native UI text, animations, runtime behavior, packaging, and rollback expectations.

Import a DLC folder or archive, inspect its integration graph, then select nodes and fields for explanations. Package Intelligence contains OIV preview, DLC inventory, vehicle-data compilation, and structured META/XML comparison and round-trip tools.

OIV preview keeps unsafe text, XPath, PSO, unbounded archive-creation, and unknown operations blocked. Bounded createIfNotExist recipes can become exactly verified managed packages, while exact adds/replacements/deletes inside existing nested RPF trees can be exported as payload-backed batch manifests. Open the matching outer archive in RPF Explorer to turn one of those manifests into a guarded atomic plan.""",
        ("authoring", "addon", "dlc", "audit", "linker", "developer"),
    ),
    HelpTopic(
        "asset-viewer", "Inspectors", "Native Asset Viewer",
        "Browse package files and preview supported native resources without executing code.",
        """Open a package folder or supported archive, search its inventory, and select an asset. Images and text preview directly. Supported Rockstar resources receive header analysis, structured CodeWalker XML, and texture contact sheets when possible.

For a supported native resource, Package actions > Export editable native workspace preserves an immutable source snapshot beside editable XML and dependencies. File > Build native workspace reconstructs the binary only after CodeWalker successfully parses the result again and writes a validation sidecar.

File > Open YTD texture workspace switches this same window into a searchable texture editor. It previews DDS images, imports DDS or common raster formats, updates width/height/mip/format metadata, supports add/replace/remove, and retains local undo history. Build + validate YTD reparses the completed dictionary before it is accepted. Raster imports become uncompressed DDS; provide a prepared DDS when compression and mip control must be retained. Compiled DLL, ASI, and script payloads are never executed.""",
        ("ytd", "ydr", "yft", "texture", "model", "preview", "codewalker"),
    ),
    HelpTopic(
        "rpf-explorer", "Inspectors", "RPF Explorer",
        "Search archives, inspect metadata, and transact guarded root or nested changes.",
        """Select the matching GTA V installation before opening an RPF so the correct encryption keys and resource decoder are used.

Search and filter the archive tree, then use Entry actions to preview or extract the selected entry. A selected directory can be exported recursively, and File > Extract current archive tree exports the root of the outer archive. Each subtree export scans the outer RPF once, writes into a new staged folder, refuses output collisions, verifies that its source did not change, and records source and per-file SHA-256 values in .allin1-rpf-export.json. Nested RPF files remain files in their parent export; select that virtual archive explicitly to export its internal tree.

File > Build new RPF from folder creates a brand-new OPEN archive outside the GTA V installation without touching an existing archive or game file. Name a loose directory example.rpf.source to author it as a nested example.rpf. The builder refuses precompiled nested RPFs, links, path collisions, output overwrite, source changes, excessive depth or size, and any result whose recursive index, directory tree, or exact extracted payload hashes differ from the source. A verified JSON report is published beside the new archive; install it later through a validated, receipt-owned package.

OIV preview recognizes bounded createIfNotExist recipes instead of executing them. The CLI's --created-rpf-package workflow extracts only declared content members into retained loose authoring sources, builds all nested archives through the same exact-verification engine, and emits a validated mod.toml package. Text/XML/PSO edits, deletes inside a new archive, missing nested creation declarations, and creation roots deeper than one existing archive remain blocked.

Supported native files expose Entry > Export editable native workspace. After editing its XML or dependencies, Entry > Plan replacement from native workspace rebuilds the binary, reparses it through CodeWalker, keeps the payload and validation report beside the inert plan, and binds their hashes to the selected root or nested entry. The archive is still unchanged until the normal reviewed apply step.

File > Compare with archive indexes both recursive trees. Metadata mode is fast and reports added, removed, modified, and unchanged archive/entry records. Exact-content mode batch-extracts each side into bounded temporary storage and compares SHA-256 values, detecting payload changes even when size and resource metadata are identical. JSON and Markdown reports are written together, and both source hashes are rechecked before the report is accepted.

Single-file replace, add, and delete planning creates an inert schema-v3 JSON plan. Atomic tree plans additionally support mkdir, explicit empty-directory rmdir, same-parent rename, and nested-RPF deletion. Both hash the archive, original state, payload where applicable, edition, and authorized scope.

File > Create multi-entry plan accepts a JSON changes list. Use new_entry for rename. Upsert becomes add or replace only after the exact target is indexed. Directory removal is non-recursive, so all descendants must be independently listed for deletion; this prevents an accidental folder selection from erasing a subtree. File > Plan new directory and Entry > Plan rename expose the common structural operations directly. A ready atomic plan snapshots every payload, creates parents before children, removes children before parents, processes each nested RPF once from deepest to outermost, commits the outer archive once, verifies every changed entry, and owns one full rollback receipt. An archive entry cannot be replaced or deleted in the same plan that edits its internal tree.

A subtree export preserves empty directories in its manifest and on disk. After editing the loose workspace, File > Plan subtree workspace sync validates the untouched source and manifest, then reconciles changed, added, and removed files and directories into one inert atomic plan.

A ready plan may be applied only inside the selected installation's mods directory or an external workspace explicitly authorized by the CLI. A nested change walks a bounded chain of up to eight RPF levels inside the staged outer copy, verifies the deepest change, and verifies every child while reassembling its parent before commit. Transaction History provides verification, interrupted-receipt reconciliation, guarded rollback, and stale-lock inspection. Run disposable archive canary proves the real writer without changing the selected source.""",
        ("archive", "nested", "extract", "replacement", "rpf", "metadata"),
    ),
    HelpTopic(
        "recovery", "Safety & recovery", "Backups and recovery",
        "Understand RPF transaction ownership, verification, and rollback.",
        """Package scans, native previews, RPF indexes, extracted copies, linker reports, and replacement-plan creation are read-only operations.

An applied RPF plan stores its complete pre-write archive, an exact payload snapshot, the reviewed plan, and a receipt under the ALLIN1 SDK user-data directory. Verify transaction receipt proves whether the archive is still applied, already original, or externally modified and checks the rollback snapshot and exact entry.

Rollback is refused if another tool changed the archive after ALLIN1 applied it. A rollback restores the complete snapshot through a staged copy and verifies the original entry before changing the receipt to rolled_back. Failed commits automatically attempt the same restoration and preserve their receipt for diagnosis.

Only one ALLIN1 transaction can own an archive at a time. Transaction History can reconcile an interrupted receipt without completing an uncommitted write. It clears an .allin1.lock only after proving that its owner process is gone, GTA V is closed, and the archive remains inside its authorized scope.""",
        ("backup", "rollback", "restore", "safety", "receipt"),
    ),
    HelpTopic(
        "troubleshooting", "Safety & recovery", "Troubleshooting and logs",
        "Resolve importer, helper, native-decoder, and package-validation failures.",
        """Confirm the package is complete, the correct GTA V edition is selected, and the helper was built from the pinned CodeWalker submodule.

An unresolved edition, missing source, unsafe archive path, checksum mismatch, or incomplete rollback step is intentionally surfaced instead of guessed. Export the audit/link report when asking for help so the exact finding codes and paths are preserved.

F1 routes the current workspace to the relevant article in this embedded Help Center.""",
        ("logs", "error", "helper", "diagnostics", "finding", "failure"),
    ),
)


def search_help_topics(query: str) -> tuple[HelpTopic, ...]:
    """Return help topics ranked by a simple, predictable text match."""
    words = tuple(part.casefold() for part in query.split() if part.strip())
    if not words:
        return HELP_TOPICS

    scored: list[tuple[int, HelpTopic]] = []
    for topic in HELP_TOPICS:
        title = topic.title.casefold()
        category = topic.category.casefold()
        summary = topic.summary.casefold()
        body = topic.body.casefold()
        keywords = " ".join(topic.keywords).casefold()
        haystack = " ".join((title, category, summary, body, keywords))
        if not all(word in haystack for word in words):
            continue
        score = sum(
            8 if word in title else 4 if word in keywords else 2 if word in summary else 1
            for word in words
        )
        scored.append((score, topic))
    return tuple(topic for _score, topic in sorted(
        scored, key=lambda item: (-item[0], item[1].category, item[1].title),
    ))


class HelpCenterDialog(ttk.Frame):
    """Searchable help center embedded in the primary SDK shell."""

    def __init__(
        self, parent: tk.Misc, initial_topic: str | None = None,
        *, embedded: bool = False,
    ) -> None:
        self._window: tk.Toplevel | None = None
        host = parent
        if not embedded:
            self._window = tk.Toplevel(parent)
            self._window.title("ALLIN1 SDK Help Center")
            self._window.geometry("1040x700")
            self._window.minsize(780, 540)
            self._window.transient(parent.winfo_toplevel())
            host = self._window
        super().__init__(host)
        self.pack(fill="both", expand=True)
        self.initial_topic = initial_topic
        self.visible_topics: tuple[HelpTopic, ...] = ()
        self.topic_items: dict[str, HelpTopic] = {}
        self._build()
        self._populate()
        if self._window is not None:
            self.bind("<Escape>", lambda _event: self._window.destroy())

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=20)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 16))
        ttk.Label(
            header, text="Help Center", font=("Segoe UI Semibold", 20),
            foreground="#173d32",
        ).pack(anchor="w")
        ttk.Label(
            header,
            text="Guidance for setup, package authoring, native assets, and recovery.",
            foreground="#52635c",
        ).pack(anchor="w", pady=(3, 0))

        body = ttk.Panedwindow(outer, orient="horizontal")
        body.pack(fill="both", expand=True)
        navigation = ttk.Frame(body, padding=(0, 0, 14, 0))
        article = ttk.Frame(body, padding=(18, 4, 4, 4))
        body.add(navigation, weight=2)
        body.add(article, weight=5)

        ttk.Label(navigation, text="Search help", style="FieldLabel.TLabel").pack(
            anchor="w",
        )
        self.query = tk.StringVar()
        search = ttk.Entry(navigation, textvariable=self.query)
        search.pack(fill="x", pady=(6, 12))
        self.query.trace_add("write", lambda *_args: self._populate())
        self.results = tk.Listbox(
            navigation, exportselection=False, activestyle="none", borderwidth=0,
            highlightthickness=1, highlightbackground="#d7e0dc",
            selectbackground="#dcefe3", selectforeground="#173d32",
            font=("Segoe UI", 10),
        )
        result_scroll = ttk.Scrollbar(
            navigation, orient="vertical", command=self.results.yview,
        )
        self.results.configure(yscrollcommand=result_scroll.set)
        self.results.pack(side="left", fill="both", expand=True)
        result_scroll.pack(side="right", fill="y")
        self.results.bind("<<ListboxSelect>>", self._select_topic)

        self.category = tk.StringVar(value="START HERE")
        self.heading = tk.StringVar(value="Select a help topic")
        self.summary = tk.StringVar(value="")
        ttk.Label(
            article, textvariable=self.category, foreground="#1f7f42",
            font=("Segoe UI Semibold", 9),
        ).pack(anchor="w")
        ttk.Label(
            article, textvariable=self.heading, font=("Segoe UI Semibold", 18),
            foreground="#173d32",
        ).pack(anchor="w", pady=(4, 2))
        ttk.Label(
            article, textvariable=self.summary, foreground="#52635c",
            wraplength=650, justify="left",
        ).pack(anchor="w", pady=(0, 12))
        ttk.Separator(article).pack(fill="x", pady=(0, 12))
        article_frame = ttk.Frame(article)
        article_frame.pack(fill="both", expand=True)
        self.body = tk.Text(
            article_frame, wrap="word", relief="flat", borderwidth=0,
            background="#ffffff", foreground="#24332d", font=("Segoe UI", 10),
            padx=4, pady=4, spacing1=3, spacing3=8, state="disabled",
        )
        article_scroll = ttk.Scrollbar(
            article_frame, orient="vertical", command=self.body.yview,
        )
        self.body.configure(yscrollcommand=article_scroll.set)
        self.body.pack(side="left", fill="both", expand=True)
        article_scroll.pack(side="right", fill="y")

    def _populate(self) -> None:
        self.visible_topics = search_help_topics(self.query.get())
        self.results.delete(0, "end")
        self.topic_items.clear()
        for index, topic in enumerate(self.visible_topics):
            label = f"{topic.category}\n   {topic.title}"
            self.results.insert("end", label)
            self.topic_items[str(index)] = topic
        if not self.visible_topics:
            self.category.set("NO RESULTS")
            self.heading.set("No matching help topics")
            self.summary.set("Try a shorter search such as ‘RPF’, ‘install’, or ‘logs’.")
            self._set_body("")
            return
        selected_index = 0
        if self.initial_topic:
            for index, topic in enumerate(self.visible_topics):
                if topic.key == self.initial_topic:
                    selected_index = index
                    break
            self.initial_topic = None
        self.results.selection_set(selected_index)
        self.results.see(selected_index)
        self._show_topic(self.visible_topics[selected_index])

    def show_topic(self, key: str) -> None:
        """Navigate an existing help workspace without opening a window."""
        self.initial_topic = key
        self.query.set("")
        self._populate()

    def _select_topic(self, _event: object | None = None) -> None:
        selection = self.results.curselection()
        if selection and selection[0] < len(self.visible_topics):
            self._show_topic(self.visible_topics[selection[0]])

    def _show_topic(self, topic: HelpTopic) -> None:
        self.category.set(topic.category.upper())
        self.heading.set(topic.title)
        self.summary.set(topic.summary)
        self._set_body(topic.body)

    def _set_body(self, value: str) -> None:
        self.body.configure(state="normal")
        self.body.delete("1.0", "end")
        self.body.insert("1.0", value)
        self.body.configure(state="disabled")
