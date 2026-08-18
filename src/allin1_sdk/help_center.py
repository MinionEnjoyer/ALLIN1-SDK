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

The SDK audits and authors content. Its RPF workspace applies reviewed replace, add, or delete plans to an exact mods copy or an explicitly isolated external workspace, including one-level nested entries. Full package installation, repair, and game launch remain the responsibility of the separate ALLIN1 Launcher.""",
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
        """The allin1-sdk command exposes package import, manifest validation, link reports, OIV plans, DLC inventory, vehicle compilation, and RPF indexing/extraction.

Commands return non-zero exit codes for invalid or unsafe inputs. Creating an entry-change plan never authorizes a write. Applying or rolling back a ready plan is a separate command and requires --acknowledge-write, a closed game, an authorized target, and unchanged input hashes. Real-archive canary mode proves replace/add/delete and rollback on a disposable copy.""",
        ("cli", "automation", "continuous integration", "script", "exit code"),
    ),
    HelpTopic(
        "console", "Automation", "SDK Console",
        "Use the complete command surface without leaving the desktop SDK.",
        """Open Tools → SDK Console or press Ctrl+`. Start typing to progressively filter commands. Suggestions include command syntax, options, local paths, and persistent command history.

Tab accepts the selected suggestion. Up and Down move through visible matches; Ctrl+Up and Ctrl+Down move through history. Enter runs the command asynchronously, Ctrl+L clears output, and Escape clears the command or closes an empty console.

The console invokes the same Click commands and safety checks as allin1-sdk in a terminal. It does not bypass target authorization, acknowledgements, hashes, locks, game-process checks, or rollback requirements. Type help for the catalog or help <command> for detailed syntax.""",
        ("console", "autocomplete", "completion", "history", "source", "terminal"),
    ),
    HelpTopic(
        "input", "Interface", "Navigating the SDK",
        "Use the integration graph, field inspector, menus, and search efficiently.",
        """Content imports are grouped under Import content. Review actions apply to the selected package, and Package intelligence contains cross-package tools.

Select an integration node to see its source, contract, and linked fields. Select a field for a plain-language explanation. Tools → SDK Console opens the in-app command surface. F1 opens contextual help; Escape closes secondary dialogs.""",
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

Import a DLC folder or archive, inspect its integration graph, then select nodes and fields for explanations. Package Intelligence contains OIV preview, DLC inventory, vehicle-data compilation, and structured META/XML comparison and round-trip tools.""",
        ("authoring", "addon", "dlc", "audit", "linker", "developer"),
    ),
    HelpTopic(
        "asset-viewer", "Inspectors", "Native Asset Viewer",
        "Browse package files and preview supported native resources without executing code.",
        """Open a package folder or supported archive, search its inventory, and select an asset. Images and text preview directly. Supported Rockstar resources receive header analysis, structured CodeWalker XML, and texture contact sheets when possible.

The viewer is read-only. Compiled DLL, ASI, and script payloads are never executed.""",
        ("ytd", "ydr", "yft", "texture", "model", "preview", "codewalker"),
    ),
    HelpTopic(
        "rpf-explorer", "Inspectors", "RPF Explorer",
        "Search archives, inspect metadata, and transact guarded root or nested changes.",
        """Select the matching GTA V installation before opening an RPF so the correct encryption keys and resource decoder are used.

Search and filter the archive tree, then use Entry actions to preview or extract the selected entry. Replace, add, and delete planning creates an inert schema-v3 JSON plan and hashes the archive, original state, payload where applicable, edition, and authorized scope.

A ready plan may be applied only inside the selected installation's mods directory or an external workspace explicitly authorized by the CLI. A nested change is performed inside the staged parent RPF and verified through that parent before commit. Transaction History provides verification, interrupted-receipt reconciliation, guarded rollback, and stale-lock inspection. Run disposable archive canary proves the real writer without changing the selected source.""",
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

F1 opens this help center in every primary SDK window.""",
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


class HelpCenterDialog(tk.Toplevel):
    """Searchable help center shared by the SDK and its inspection tools."""

    def __init__(self, parent: tk.Misc, initial_topic: str | None = None) -> None:
        super().__init__(parent)
        self.initial_topic = initial_topic
        self.visible_topics: tuple[HelpTopic, ...] = ()
        self.topic_items: dict[str, HelpTopic] = {}
        self.title("ALLIN1 Help Center")
        self.geometry("1040x700")
        self.minsize(780, 540)
        self.transient(parent)
        self._build()
        self._populate()
        self.bind("<Escape>", lambda _event: self.destroy())

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
