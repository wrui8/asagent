use serde::Serialize;
use tauri_plugin_updater::UpdaterExt;

/// Windows Credential Manager 中的服务名 / 用户名。
const KEYRING_SERVICE: &str = "com.asagent.desktop";
const KEYRING_USER: &str = "refresh_token";

/// 构建期由 build.rs 从 desktop/api-base.txt 写入；客户端不可修改。
const BUILT_IN_API_BASE: &str = env!("ASAGENT_API_BASE_BUILD");

fn normalize_base(value: &str) -> String {
    value.trim().trim_end_matches('/').to_string()
}

/// 服务器地址在打包时写死。仅保留进程级 ASAGENT_API_BASE 覆盖，
/// 供本地联调/运维使用，界面不向最终用户暴露入口。
#[tauri::command]
fn get_api_base() -> String {
    if let Ok(value) = std::env::var("ASAGENT_API_BASE") {
        let base = normalize_base(&value);
        if !base.is_empty() {
            return base;
        }
    }
    normalize_base(BUILT_IN_API_BASE)
}

#[tauri::command]
fn store_refresh_token(token: String) -> Result<(), String> {
    if token.is_empty() {
        return Err("刷新令牌为空".to_string());
    }
    keyring::Entry::new(KEYRING_SERVICE, KEYRING_USER)
        .map_err(|error| error.to_string())?
        .set_password(&token)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn load_refresh_token() -> Result<Option<String>, String> {
    let entry =
        keyring::Entry::new(KEYRING_SERVICE, KEYRING_USER).map_err(|error| error.to_string())?;
    match entry.get_password() {
        Ok(value) => Ok(Some(value)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(error) => Err(error.to_string()),
    }
}

#[tauri::command]
fn clear_refresh_token() -> Result<(), String> {
    let entry =
        keyring::Entry::new(KEYRING_SERVICE, KEYRING_USER).map_err(|error| error.to_string())?;
    match entry.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(error) => Err(error.to_string()),
    }
}

#[derive(Serialize)]
struct DesktopUpdateInfo {
    current_version: String,
    version: String,
    notes: Option<String>,
}

/// 检查 GitHub Release 的 latest.json；没有更新时返回 None。
#[tauri::command]
async fn check_desktop_update(
    app: tauri::AppHandle,
) -> Result<Option<DesktopUpdateInfo>, String> {
    let updater = app.updater().map_err(|error| error.to_string())?;
    let update = updater.check().await.map_err(|error| error.to_string())?;
    Ok(update.map(|update| DesktopUpdateInfo {
        current_version: update.current_version.to_string(),
        version: update.version.clone(),
        notes: update.body.clone(),
    }))
}

/// 下载并安装更新；NSIS 安装程序接管后会退出当前进程。
#[tauri::command]
async fn install_desktop_update(app: tauri::AppHandle) -> Result<(), String> {
    let updater = app.updater().map_err(|error| error.to_string())?;
    let Some(update) = updater.check().await.map_err(|error| error.to_string())? else {
        return Ok(());
    };
    update
        .download_and_install(|_chunk, _content_length| {}, || {})
        .await
        .map_err(|error| error.to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            get_api_base,
            store_refresh_token,
            load_refresh_token,
            clear_refresh_token,
            check_desktop_update,
            install_desktop_update
        ])
        .run(tauri::generate_context!())
        .expect("asagent desktop failed to start");
}
