use serde::Serialize;
use std::path::{Path, PathBuf};

#[derive(Clone, Debug, Serialize)]
pub struct LaunchRequest {
    pub workspace: String,
    pub source: Option<String>,
    pub selection: Option<String>,
    pub category: Option<String>,
    pub warning: Option<String>,
}

fn value_after(args: &[String], flag: &str) -> Option<String> {
    args.iter()
        .position(|item| item == flag)
        .and_then(|index| args.get(index + 1))
        .cloned()
}

fn resolve_source(value: String, cwd: &Path) -> Result<String, String> {
    if value.contains('\0') {
        return Err("launch path contains a NUL byte".to_string());
    }
    let authored = PathBuf::from(value);
    let candidate = if authored.is_absolute() {
        authored
    } else {
        cwd.join(authored)
    };
    candidate
        .canonicalize()
        .map(|path| path.to_string_lossy().into_owned())
        .map_err(|error| format!("launch source could not be resolved: {error}"))
}

pub fn parse_launch_args(args: &[String], cwd: &Path) -> Option<LaunchRequest> {
    let routes: [(&str, &str, Option<&str>, Option<&str>); 9] = [
        ("--addon-manifest", "linker", None, None),
        ("--asset-source", "assets", None, None),
        ("--rpf-archive", "rpf", None, None),
        (
            "--rpf-graph",
            "rpf",
            Some("graph-node"),
            None,
        ),
        (
            "--vehicle-package",
            "workbench",
            None,
            None,
        ),
        (
            "--axle-workspace",
            "workbench",
            Some("axle-model"),
            None,
        ),
        (
            "--workbench-package",
            "workbench",
            None,
            None,
        ),
        (
            "--model-material-source",
            "models",
            None,
            None,
        ),
        (
            "--map-project",
            "workbench",
            None,
            None,
        ),
    ];
    for (flag, workspace, selection_name, _warning) in routes {
        if let Some(value) = value_after(args, flag) {
            let source = resolve_source(value, cwd);
            let source_warning = source.as_ref().err().cloned();
            return Some(LaunchRequest {
                workspace: workspace.to_string(),
                source: source.ok(),
                selection: selection_name.and_then(|name| value_after(args, &format!("--{name}"))),
                category: if flag == "--workbench-package" {
                    value_after(args, "--workbench-category")
                } else if flag == "--vehicle-package" || flag == "--axle-workspace" {
                    Some("vehicles".to_string())
                } else if flag == "--rpf-graph" {
                    Some("graph".to_string())
                } else if flag == "--map-project" {
                    Some("maps".to_string())
                } else {
                    None
                },
                warning: source_warning,
            });
        }
    }
    if let Some(workspace) = value_after(args, "--workspace") {
        if ["linker", "assets", "workbench", "receipts", "quick_import", "models", "rpf", "recipes", "data_tools", "help", "assistant"].contains(&workspace.as_str()) {
            let assistant = workspace == "assistant";
            return Some(LaunchRequest { workspace: if assistant { "linker".into() } else { workspace }, source: None, selection: None,
                category: if assistant { Some("assistant".into()) } else { None }, warning: None });
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn direct_manifest_routes_to_linker() {
        let cwd = std::env::current_dir().unwrap();
        let source = cwd.join("Cargo.toml");
        let args = vec![
            "ALLIN1-SDK.exe".to_string(),
            "--addon-manifest".to_string(),
            source.to_string_lossy().into_owned(),
        ];
        let request = parse_launch_args(&args, &cwd).unwrap();
        assert_eq!(request.workspace, "linker");
        assert!(request.source.is_some());
    }

    #[test]
    fn direct_vehicle_package_routes_to_vehicle_inspection() {
        let cwd = std::env::current_dir().unwrap();
        let source = cwd.join("Cargo.toml");
        let args = vec![
            "ALLIN1-SDK.exe".to_string(),
            "--vehicle-package".to_string(),
            source.to_string_lossy().into_owned(),
        ];
        let request = parse_launch_args(&args, &cwd).unwrap();
        assert_eq!(request.workspace, "workbench");
        assert_eq!(request.category.as_deref(), Some("vehicles"));
        assert!(request.warning.is_none());
    }

    #[test]
    fn launcher_workspace_routes_are_allowlisted() {
        let cwd = std::env::current_dir().unwrap();
        for workspace in ["assets", "rpf", "assistant"] {
            let args = vec!["sdk.exe".into(), "--workspace".into(), workspace.into()];
            let result = parse_launch_args(&args, &cwd).unwrap();
            assert_eq!(result.workspace, if workspace == "assistant" { "linker" } else { workspace });
            assert!(result.warning.is_none());
        }
        assert!(parse_launch_args(&["sdk.exe".into(), "--workspace".into(), "shell".into()], &cwd).is_none());
    }
}
