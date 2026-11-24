import { Download, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";

interface InstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

const DISMISS_KEY = "asagent.installDismissed";

/** PWA 安装提示条（Chrome / Edge / Android）。 */
export function InstallBar() {
  const { pathname } = useLocation();
  const [event, setEvent] = useState<InstallPromptEvent | null>(null);
  const [visible, setVisible] = useState(false);
  // 对话页底部有输入区，安装条需要抬到输入区上方，避免遮挡发送按钮
  const [bottom, setBottom] = useState(18);

  useEffect(() => {
    const standalone =
      window.matchMedia?.("(display-mode: standalone)").matches ?? false;
    if (standalone || localStorage.getItem(DISMISS_KEY) === "1") return;

    const onPrompt = (raw: Event) => {
      raw.preventDefault();
      setEvent(raw as InstallPromptEvent);
      setVisible(true);
    };
    window.addEventListener("beforeinstallprompt", onPrompt);
    return () => window.removeEventListener("beforeinstallprompt", onPrompt);
  }, []);

  useEffect(() => {
    if (!visible) return;
    const composer = document.querySelector<HTMLElement>(".composer");
    if (!composer) {
      setBottom(18);
      return;
    }
    const update = () => setBottom(composer.offsetHeight + 12);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(composer);
    return () => observer.disconnect();
  }, [visible, pathname]);

  if (!visible || !event) return null;

  const install = async () => {
    await event.prompt();
    const choice = await event.userChoice;
    if (choice.outcome === "accepted") setVisible(false);
  };

  const dismiss = () => {
    setVisible(false);
    localStorage.setItem(DISMISS_KEY, "1");
  };

  return (
    <div className="install-bar" style={{ bottom }}>
      <Download size={15} style={{ color: "var(--cyan)" }} />
      <span>安装 asagent 到桌面，获得独立窗口与离线缓存</span>
      <button className="btn btn-primary btn-sm" onClick={() => void install()}>
        安装
      </button>
      <button className="icon-btn" style={{ width: 26, height: 26 }} onClick={dismiss} title="暂不安装">
        <X size={13} />
      </button>
    </div>
  );
}
