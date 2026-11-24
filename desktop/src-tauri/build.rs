use std::path::Path;

/// 把 desktop/api-base.txt 中的服务地址烧进二进制。
/// 文件是唯一「写死」来源；文件缺失时允许构建环境变量兜底（仅本地调试）。
fn main() {
    let config = Path::new("../api-base.txt");
    println!("cargo:rerun-if-changed={}", config.display());
    println!("cargo:rerun-if-env-changed=ASAGENT_API_BASE");

    let from_file = std::fs::read_to_string(config)
        .ok()
        .map(|value| value.trim_start_matches('\u{feff}').trim().to_string())
        .filter(|value| !value.is_empty());
    let from_env = std::env::var("ASAGENT_API_BASE")
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty());
    let api_base = from_file.or(from_env).unwrap_or_default();

    if !api_base.is_empty()
        && !(api_base.starts_with("http://") || api_base.starts_with("https://"))
    {
        panic!("desktop/api-base.txt 必须以 http:// 或 https:// 开头：{api_base}");
    }
    println!("cargo:rustc-env=ASAGENT_API_BASE_BUILD={api_base}");

    tauri_build::build()
}
