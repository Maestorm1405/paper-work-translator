// Web version: there is no server, so the page's /api requests are answered by the app's own Python code,
// running in the browser with Pyodide. Nothing is uploaded anywhere.
(() => {
  const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/";
  const FILES = ["__init__.py", "app.py", "translator.py", "checks.py", "local_reader.py",
    "samples/discharge.json", "samples/bill.json", "samples/denial.json"];
  const realFetch = window.fetch.bind(window);
  let ready = null;

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = src;
      script.onload = resolve;
      script.onerror = () => reject(new Error("The app couldn't load. Check your internet and reload the page."));
      document.head.append(script);
    });
  }

  function start() {
    ready ??= (async () => {
      await loadScript(`${PYODIDE}pyodide.js`);
      const py = await window.loadPyodide({ indexURL: PYODIDE });
      await py.loadPackage(["pydantic", "micropip"]);
      py.FS.mkdirTree("/app/paperwork_translator/samples");
      await Promise.all(FILES.map(async (name) => {
        const response = await realFetch(`py/paperwork_translator/${name}`);
        py.FS.writeFile(`/app/paperwork_translator/${name}`, await response.text());
      }));
      try {
        await py.pyimport("micropip").install("pypdf");
      } catch {
        // PDFs won't open, but photos, pasted words, and samples still work.
      }
      py.runPython(await (await realFetch("py/web_api.py")).text());
      return py;
    })();
    return ready;
  }

  window.fetch = async (input, init = {}) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    const route = url.pathname.match(/\/api\/(config|translate|ask)$/)?.[1];
    if (!route) return realFetch(input, init);

    const status = document.getElementById("status");
    if (route === "config" && status) status.textContent = "Getting the app ready. This takes a few seconds the first time.";
    try {
      const py = await start();
      let result;
      if (route === "config") {
        result = py.globals.get("web_config")();
      } else if (route === "ask") {
        result = py.globals.get("web_ask")(init.body);
      } else {
        const form = init.body;
        const pdfs = [];
        for (const file of form.getAll("files")) pdfs.push(new Uint8Array(await file.arrayBuffer()));
        result = py.globals.get("web_translate")(form.get("text") || "", pdfs, form.get("sample_id") || "",
          form.get("language") || "en", form.get("reading_level") || "simple");
      }
      const [code, body] = result.toJs();
      result.destroy();
      return new Response(body, { status: code, headers: { "Content-Type": "application/json" } });
    } finally {
      if (route === "config" && status) status.textContent = "";
    }
  };
})();
