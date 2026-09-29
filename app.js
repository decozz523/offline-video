/* Sea Louder — вставил ссылку, скачал файл, смотришь офлайн.
   Скачивание делает локальный server.py (yt-dlp), страница только показывает прогресс. */
const $ = (id) => document.getElementById(id);

const form = $("form"), input = $("urlInput");
const card = $("playerCard"), wrap = $("embedWrap"), caption = $("caption");
const loader = $("loader"), errBox = $("errorBox");
const dlChip = $("dlChip"), audioChip = $("audioChip"), origChip = $("origChip");
const lib = $("library"), libList = $("libList"), libDir = $("libDir"), libCount = $("libCount");
const serverBox = $("serverBox"), refreshBtn = $("refreshBtn"), hdSelect = $("hdSelect");

const API = "";                       // локальный сервер на том же адресе: http://127.0.0.1:8756
let online = false;                   // доступен ли локальный сервер
let lastUrl = "";
let polling = null, progWrap = null;

/* ---------- определение платформы ---------- */
function detect(url) {
	try { url = new URL(url.trim()); } catch { return null; }
	const h = url.hostname.replace(/^www\.|^m\.|^mobile\./, "");
	if (h === "youtu.be") return { platform: "youtube", id: url.pathname.slice(1), url: url.href };
	if (h.endsWith("youtube.com")) {
		if (url.pathname === "/watch") return { platform: "youtube", id: url.searchParams.get("v"), url: url.href };
		if (url.pathname.startsWith("/shorts/")) return { platform: "youtube", id: url.pathname.split("/")[2], url: url.href };
		if (url.pathname.startsWith("/embed/")) return { platform: "youtube", id: url.pathname.split("/")[2], url: url.href };
	}
	if (h === "x.com" || h === "twitter.com") return { platform: "twitter", url: url.href };
	if (h.endsWith("vk.com") || h.endsWith("vkvideo.ru")) return { platform: "vk", url: url.href };
	if (h.endsWith("reddit.com")) return { platform: "reddit", url: url.href };
	if (h.endsWith("tiktok.com")) return { platform: "tiktok", url: url.href };
	if (h.endsWith("instagram.com")) return { platform: "instagram", url: url.href };
	return { platform: "other", url: url.href };
}

/* ---------- встроенный просмотр ---------- */
function embedYouTube(id) {
	const f = document.createElement("iframe");
	f.className = "embed-frame";
	f.allow = "accelerometer; autoplay; encrypted-media; picture-in-picture";
	f.src = `https://www.youtube-nocookie.com/embed/${encodeURIComponent(id)}?rel=0`;
	wrap.replaceChildren(f);
	return true;
}

function loadVk(url) {
	const m = url.match(/-(\d+)_(\d+)/);
	if (m) {
		const f = document.createElement("iframe");
		f.className = "embed-frame";
		f.allow = "autoplay; encrypted-media; fullscreen";
		f.src = `https://vk.com/video_ext.php?oid=-${m[1]}&id=${m[2]}&hd=2`;
		wrap.replaceChildren(f);
		return true;
	}
	wrap.innerHTML = `<p class="caption">VK не всегда разрешает встраивание.<br>Открой ссылку напрямую или нажми «Сохранить видео».</p>`;
	return true;
}

async function loadTwitter(url) {
	// публичный эндпоинт синдикации: отдаёт прямую mp4-ссылку без ключей
	const m = url.match(/status\/(\d+)/);
	if (!m) throw new Error("Не найден ID поста в ссылке X / Twitter.");
	const res = await fetch(`https://cdn.syndication.twimg.com/tweet-result?id=${m[1]}&lang=ru&token=x`);
	if (!res.ok) throw new Error("X/Twitter не отдал данные поста (возможно, пост удалён или приватный).");
	const data = await res.json();
	caption.textContent = `${data.user?.name ?? ""} — ${data.text ?? ""}`;
	const vid = data.mediaDetails?.find(v => v.type === "video" || v.type === "animated_gif");
	const best = vid?.video_info?.variants
		?.filter(v => v.content_type === "video/mp4")
		?.sort((a, b) => (b.bitrate ?? 0) - (a.bitrate ?? 0))[0];
	if (best) {
		const v = document.createElement("video");
		v.src = best.url; v.controls = true; v.className = "embed-frame";
		wrap.replaceChildren(v);
		return true;
	}
	const f = document.createElement("iframe");
	f.className = "embed-frame";
	f.src = `https://twitframe.com/show?url=${encodeURIComponent(url)}`;
	wrap.replaceChildren(f);
	return true;
}

async function loadReddit(url) {
	const j = await fetch(url.replace(/\/?$/, "") + ".json");
	if (!j.ok) throw new Error("Reddit не отдал данные поста.");
	const data = (await j.json())[0].data.children[0].data;
	caption.textContent = `r/${data.subreddit} — ${data.title}`;
	const media = data.secure_media?.reddit_video || data.preview?.reddit_video;
	if (media?.fallback_url) {
		const v = document.createElement("video");
		v.src = media.fallback_url; v.controls = true; v.className = "embed-frame";
		wrap.replaceChildren(v);
		return true;
	}
	if (data.url && /\.(gif|jpe?g|png|webp)$/i.test(data.url)) {
		wrap.innerHTML = `<img src="${data.url}" style="max-width:100%;border-radius:12px">`;
		return true;
	}
	throw new Error("Не удалось вытащить видео из поста Reddit.");
}

/* ---------- связь с локальным сервером ---------- */
async function pingServer() {
	try {
		const r = await fetch(API + "/api/health", { cache: "no-store" });
		online = r.ok;
		if (online) {
			const h = await r.json();
			serverBox.classList.add("hidden");
			if (!h.ytdlp) {
				serverBox.innerHTML = "⚠ Сервер работает, но <b>yt-dlp</b> не установлен. Выполни: <code>python -m pip install -U yt-dlp</code>";
				serverBox.classList.remove("hidden");
			}
		}
	} catch { online = false; }
	if (!online) {
		serverBox.innerHTML = `⚠ Локальный сервер не запущен — <b>скачать файл нельзя</b>.<br>
			Запусти <code>start.bat</code> (или <code>python server.py</code>) и обнови страницу.<br>
			Просмотр во встроенном плеере ниже работает и без сервера.`;
		serverBox.classList.remove("hidden");
	}
	return online;
}

async function downloadViaApi(url, mode) {
	if (!(await pingServer())) return fail("Локальный сервер не запущен: скачивать некому. Запусти start.bat и обнови страницу.");
	hideErr();

	const btn = mode === "audio" ? audioChip : dlChip;
	const label = btn.textContent;
	btn.textContent = "⏳ Готовим файл…";

	try {
		const r = await fetch(API + "/api/download", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ url, mode, quality: mode === "audio" ? "best" : (hdSelect?.value || "best") })
		});
		const data = await r.json();
		if (!r.ok || data.status !== "working") throw new Error(data.error || "Сервер не принял задачу.");
		await pollJob(data.id, btn, label);
	} catch (e) {
		btn.textContent = label;
		fail("Скачивание не удалось: " + (e.message || e));
	}
}

function pollJob(id, btn, label) {
	return new Promise(resolve => {
		if (polling) clearInterval(polling);
		polling = setInterval(async () => {
			let j;
			try {
				const r = await fetch(API + "/api/status?id=" + id, { cache: "no-store" });
				j = await r.json();
			} catch { return; }
			if (j.status === "working") {
				btn.textContent = `⏳ ${j.percent > 0 ? j.percent + "%" : "загрузка…"}`;
				return;
			}
			clearInterval(polling); polling = null;
			btn.textContent = label;
			if (j.status === "done") {
				const size = j.size ? humanSize(j.size) : "";
				showNote(`✅ Файл сохранён: <b>${j.file}</b> ${size}<br>Папка: ${libDir.textContent || "downloads"} — смотри его без интернета.`);
				loadLibrary();
			} else {
				fail("yt-dlp не справился:<br>" + (j.error || "неизвестная ошибка"));
			}
			resolve();
		}, 600);
	});
}

/* ---------- библиотека загрузок (офлайн-просмотр) ---------- */
function humanSize(n) {
	const u = ["Б", "КБ", "МБ", "ГБ"];
	let i = 0;
	while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
	return `${i ? n.toFixed(1) : n} ${u[i]}`;
}

async function loadLibrary() {
	if (!(await pingServer())) return;
	try {
		const r = await fetch(API + "/api/downloads", { cache: "no-store" });
		const data = await r.json();
		lib.classList.remove("hidden");
		libDir.textContent = data.dir;
		libCount.textContent = data.items.length;
		if (!data.items.length) {
			libList.innerHTML = `<p class="caption">Пока пусто. Вставь ссылку и нажми «Сохранить видео» — файл появится здесь.</p>`;
			return;
		}
		libList.replaceChildren(...data.items.map(item => {
			const row = document.createElement("div");
			row.className = "lib-item";
			const src = API + item.url;
			row.innerHTML = `
				<div class="lib-info">
					<span class="lib-kind">${item.kind === "audio" ? "🎵" : "🎬"}</span>
					<span class="lib-name" title="${item.name}">${item.name}</span>
					<span class="lib-size">${item.sizeText}</span>
				</div>
				<video class="lib-player" controls preload="metadata" src="${src}"></video>
				<div class="lib-actions">
					<a class="chip" href="${API}/api/file/${encodeURIComponent(item.name)}">⬇ Скачать файл</a>
					<button class="chip" type="button">▶ Показать в плеере</button>
				</div>`;
			const player = row.querySelector(".lib-player");
			player.style.display = "none";
			const toggle = row.querySelector(".lib-actions button");
			toggle.onclick = () => {
				const shown = player.style.display !== "none";
				player.style.display = shown ? "none" : "block";
				toggle.textContent = shown ? "▶ Показать в плеере" : "▲ Скрыть плеер";
				if (!shown) player.play().catch(() => {});
			};
			return row;
		}));
	} catch {
		lib.classList.add("hidden");
	}
}

/* ---------- основной сценарий ---------- */
async function run() {
	const raw = input.value.trim();
	if (!raw) return;
	hideAll();
	loader.classList.remove("hidden");
	lastUrl = raw;
	const d = detect(raw);
	if (!d) { loader.classList.add("hidden"); return fail("Не похоже на ссылку. Проверь адрес."); }
	origChip.href = raw; origChip.classList.remove("hidden");
	dlChip.classList.add("hidden"); audioChip.classList.add("hidden");
	dlChip.onclick = () => downloadViaApi(lastUrl, "video");
	audioChip.onclick = () => downloadViaApi(lastUrl, "audio");

	try {
		switch (d.platform) {
			case "youtube":
				caption.textContent = "YouTube · " + d.id;
				embedYouTube(d.id);
				break;
			case "vk": await loadVk(d.url); break;
			case "twitter": await loadTwitter(d.url); break;
			case "reddit": await loadReddit(d.url); break;
			case "tiktok":
			case "instagram":
				caption.textContent = d.platform === "tiktok" ? "TikTok" : "Instagram";
				wrap.innerHTML = `<p class="caption">У этих платформ закрытый плеер — смотреть получится только после сохранения.<br>
					Нажми <b>«Сохранить видео»</b>, файл попадёт в блок «Мои загрузки» и откроется без интернета.</p>`;
				break;
			default:
				caption.textContent = "Неизвестный сайт";
				wrap.innerHTML = `<p class="caption">Плеера для этого сайта нет, но yt-dlp попробует скачать файл.</p>`;
		}
		loader.classList.add("hidden");
		card.classList.remove("hidden");
		dlChip.classList.remove("hidden");
		audioChip.classList.remove("hidden");
	} catch (e) {
		loader.classList.add("hidden");
		card.classList.remove("hidden");
		caption.textContent = "";
		wrap.innerHTML = `<p class="caption">Встроенный просмотр не сработал, но скачать всё равно можно.</p>`;
		dlChip.classList.remove("hidden");
		audioChip.classList.remove("hidden");
		showNote("⚠ " + (e.message || e));
	}
}

/* ---------- вспомогательное ---------- */
function fail(msg) {
	loader.classList.add("hidden");
	errBox.innerHTML = `⚠ ${msg}`;
	errBox.classList.remove("hidden");
}
function showNote(html) {
	if (progWrap) progWrap.remove();
	progWrap = document.createElement("div");
	progWrap.className = "card note";
	progWrap.innerHTML = html;
	card.after(progWrap);
}
function hideErr() { errBox.classList.add("hidden"); }
function hideAll() {
	card.classList.add("hidden");
	errBox.classList.add("hidden");
	loader.classList.add("hidden");
	if (progWrap) { progWrap.remove(); progWrap = null; }
}

form.addEventListener("submit", e => { e.preventDefault(); run(); });
refreshBtn.addEventListener("click", loadLibrary);

/* на старте: проверить сервер и показать уже скачанное (список виден и офлайн) */
pingServer().then(loadLibrary);