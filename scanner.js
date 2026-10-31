// Camera scanner: live camera view, take a picture of each page, and check each picture is clear enough to read.

const MAX_SIDE = 2576; // Claude's largest image size
const TOO_DARK = 60;
const TOO_BRIGHT = 235;
const BLURRY = 120;

export const cameraSupported = Boolean(navigator.mediaDevices?.getUserMedia) && window.isSecureContext;

// Brightness (0-255) and sharpness (how much fine detail, like letters, the picture has) of an image.
export function analyzeImage(source) {
  const width = 320;
  const height = Math.max(1, Math.round((source.height / source.width) * width));
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(source, 0, 0, width, height);
  const { data } = ctx.getImageData(0, 0, width, height);

  const gray = new Float32Array(width * height);
  let total = 0;
  for (let i = 0; i < gray.length; i++) {
    gray[i] = 0.299 * data[i * 4] + 0.587 * data[i * 4 + 1] + 0.114 * data[i * 4 + 2];
    total += gray[i];
  }
  // Sharpness is the variance of the Laplacian: blurry pictures have little edge detail.
  let sum = 0;
  let sumSquares = 0;
  let count = 0;
  for (let y = 1; y < height - 1; y++) {
    for (let x = 1; x < width - 1; x++) {
      const i = y * width + x;
      const v = 4 * gray[i] - gray[i - 1] - gray[i + 1] - gray[i - width] - gray[i + width];
      sum += v;
      sumSquares += v * v;
      count++;
    }
  }
  const mean = sum / count;
  return { brightness: total / gray.length, sharpness: sumSquares / count - mean * mean };
}

// A short, plain description of what could be better, or null if the picture looks fine.
export function qualityProblem({ brightness, sharpness }) {
  if (brightness < TOO_DARK) return "It looks too dark. Try turning on a light.";
  if (brightness > TOO_BRIGHT) return "It looks too bright. Try moving away from the glare.";
  if (sharpness < BLURRY) return "It looks blurry. Hold the camera still and try again.";
  return null;
}

export function createScanner({ elements, onPage, onClose, speak }) {
  const { section, heading, video, status, error, takeButton, torchButton, switchButton, doneButton, filesButton, count, flash } = elements;
  let stream = null;
  let cameras = [];
  let cameraIndex = 0;
  let pagesThisTime = 0;
  let torchOn = false;

  function say(message) {
    status.textContent = message;
    speak(message);
  }

  function showError(message) {
    error.textContent = message;
    error.hidden = false;
    filesButton.hidden = false;
    takeButton.disabled = true;
    speak(message);
  }

  function stopStream() {
    if (stream) for (const track of stream.getTracks()) track.stop();
    stream = null;
    video.srcObject = null;
  }

  async function startStream() {
    stopStream();
    const deviceId = cameras[cameraIndex]?.deviceId;
    const video_constraints = {
      width: { ideal: 3840 },
      height: { ideal: 2160 },
      ...(deviceId ? { deviceId: { exact: deviceId } } : { facingMode: { ideal: "environment" } }),
    };
    stream = await navigator.mediaDevices.getUserMedia({ video: video_constraints, audio: false });
    video.srcObject = stream;
    await video.play().catch(() => {});

    const track = stream.getVideoTracks()[0];
    const capabilities = track.getCapabilities?.() || {};
    torchButton.hidden = !capabilities.torch;
    torchOn = false;
    torchButton.setAttribute("aria-pressed", "false");
  }

  async function open() {
    pagesThisTime = 0;
    count.textContent = "";
    error.hidden = true;
    filesButton.hidden = true;
    takeButton.disabled = false;
    section.hidden = false;
    heading.focus();

    if (!cameraSupported) {
      showError("This device can't show the camera here. You can choose photos of your paper instead.");
      return;
    }
    say("Starting the camera.");
    try {
      await startStream();
      // Device names are only available after permission is given.
      cameras = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput");
      switchButton.hidden = cameras.length < 2;
      say("The camera is on. Lay the page flat in good light, fill the screen with it, then press Take picture.");
    } catch (err) {
      const blocked = err.name === "NotAllowedError" || err.name === "SecurityError";
      showError(blocked
        ? "The camera is blocked. Allow camera access for this app in your computer's settings (on a Mac: System Settings, Privacy and Security, Camera), then try again. Or choose photos instead."
        : err.name === "NotFoundError" || err.name === "OverconstrainedError"
          ? "No camera was found. You can choose photos of your paper instead."
          : "The camera couldn't start. Close other apps that use the camera and try again, or choose photos instead.");
    }
  }

  function close() {
    stopStream();
    section.hidden = true;
    onClose(pagesThisTime);
  }

  async function takePicture() {
    if (!stream || !video.videoWidth) {
      say("The camera isn't ready yet. Please wait a moment.");
      return;
    }
    const scale = Math.min(1, MAX_SIDE / Math.max(video.videoWidth, video.videoHeight));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(video.videoWidth * scale);
    canvas.height = Math.round(video.videoHeight * scale);
    canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);

    if (!matchMedia("(prefers-reduced-motion: reduce)").matches) {
      flash.classList.remove("flash-on");
      void flash.offsetWidth; // restart the animation
      flash.classList.add("flash-on");
    }

    const problem = qualityProblem(analyzeImage(canvas));
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.9));
    if (!blob) {
      say("That picture didn't work. Please try again.");
      return;
    }
    pagesThisTime += 1;
    const pageNumber = onPage(new File([blob], `page-${Date.now()}.jpg`, { type: "image/jpeg" }), problem);
    count.textContent = `${pagesThisTime} ${pagesThisTime === 1 ? "page" : "pages"} scanned so far.`;
    say(problem
      ? `Page ${pageNumber} added. ${problem} You can take it again, and remove the unclear one later.`
      : `Page ${pageNumber} added. It looks clear. Take the next page, or press Done.`);
  }

  async function toggleTorch() {
    const track = stream?.getVideoTracks()[0];
    if (!track) return;
    torchOn = !torchOn;
    try {
      await track.applyConstraints({ advanced: [{ torch: torchOn }] });
      torchButton.setAttribute("aria-pressed", String(torchOn));
    } catch {
      torchOn = false;
      torchButton.hidden = true;
    }
  }

  async function switchCamera() {
    cameraIndex = (cameraIndex + 1) % cameras.length;
    try {
      await startStream();
      say(`Switched to ${cameras[cameraIndex].label || `camera ${cameraIndex + 1}`}.`);
    } catch {
      showError("That camera couldn't start. Try the other one, or choose photos instead.");
    }
  }

  takeButton.addEventListener("click", takePicture);
  torchButton.addEventListener("click", toggleTorch);
  switchButton.addEventListener("click", switchCamera);
  doneButton.addEventListener("click", close);
  section.addEventListener("keydown", (event) => {
    if (event.key === "Escape") close();
  });

  return { open, close };
}
