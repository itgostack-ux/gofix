// Copyright (c) 2026, GoStack and contributors
/**
 * A camera that works on a laptop.
 *
 * `<input type="file" capture="environment">` is defined by HTML Media Capture
 * and is honoured only on devices that have a capture mechanism wired to the
 * file picker -- phones and tablets. Every desktop browser ignores it, so a
 * "Take Photo" button built that way opens a file dialog on a laptop and can
 * never reach the webcam. The counter machine is a laptop.
 *
 * So the camera is opened directly: getUserMedia gives a live stream, the
 * operator frames the device and presses the shutter, and the frame is drawn
 * to a canvas and handed back as a File -- the same shape the file input
 * produces, so every caller's upload path is unchanged.
 *
 * Falls back rather than fails. getUserMedia needs a secure context (https, or
 * localhost) and the user's permission, and a machine may have no camera at
 * all. Each of those is a reason to quietly hand the caller back to the file
 * picker, which is why capture() REJECTS with a reason instead of showing its
 * own error: only the caller knows what the fallback should be.
 */

frappe.provide("gofix.camera");

gofix.camera = {
	/** Can this machine open a camera at all? */
	is_supported() {
		return Boolean(
			window.isSecureContext &&
			navigator.mediaDevices &&
			typeof navigator.mediaDevices.getUserMedia === "function"
		);
	},

	/**
	 * Open the camera and let the operator take one or more photos.
	 *
	 * @param {Object} opts
	 * @param {string} opts.title       Dialog heading.
	 * @param {string} opts.facing      "environment" (rear, default) or "user".
	 * @param {boolean} opts.multiple   Keep the camera open after each shot.
	 * @returns {Promise<File[]>}       Resolves with what was taken (possibly
	 *                                  empty if the operator closed it), or
	 *                                  rejects with {reason} when the camera
	 *                                  could not be opened.
	 */
	capture(opts = {}) {
		const title = opts.title || __("Take Photo");
		const facing = opts.facing || "environment";
		const multiple = opts.multiple !== false;

		if (!this.is_supported()) {
			// Name the actual reason. "Camera unavailable" sends someone to
			// check their webcam when the real problem is that the site is
			// being served over plain http, where no browser will allow it.
			return Promise.reject({
				reason: window.isSecureContext ? "unsupported" : "insecure",
				message: window.isSecureContext
					? __("This browser cannot open a camera.")
					: __("A camera can only be opened over HTTPS."),
			});
		}

		return new Promise((resolve, reject) => {
			let stream = null;
			let settled = false;
			const taken = [];

			const dialog = new frappe.ui.Dialog({
				title: title,
				size: "large",
				fields: [{ fieldtype: "HTML", fieldname: "body" }],
				primary_action_label: __("Capture"),
				primary_action: () => shoot(),
				secondary_action_label: __("Done"),
				secondary_action: () => dialog.hide(),
			});

			dialog.fields_dict.body.$wrapper.html(`
				<div class="gofix-cam">
					<video class="gofix-cam-video" autoplay playsinline muted
						style="width:100%;max-height:52vh;background:#000;border-radius:6px"></video>
					<canvas class="gofix-cam-canvas" style="display:none"></canvas>
					<div class="gofix-cam-strip"
						style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px"></div>
					<div class="gofix-cam-hint text-muted"
						style="font-size:11px;margin-top:6px"></div>
				</div>`);

			const $body = dialog.fields_dict.body.$wrapper;
			const video = $body.find("video")[0];
			const canvas = $body.find("canvas")[0];
			const $strip = $body.find(".gofix-cam-strip");
			const $hint = $body.find(".gofix-cam-hint");

			// The webcam light staying on after the dialog closes is how people
			// lose trust in a system. Tracks are stopped on every exit path.
			const stop = () => {
				if (stream) {
					stream.getTracks().forEach((t) => t.stop());
					stream = null;
				}
			};

			const shoot = () => {
				if (!video.videoWidth) return;
				canvas.width = video.videoWidth;
				canvas.height = video.videoHeight;
				canvas.getContext("2d").drawImage(video, 0, 0);
				canvas.toBlob((blob) => {
					if (!blob) return;
					const name = `camera-${frappe.datetime.now_datetime()
						.replace(/[^0-9]/g, "")}-${taken.length + 1}.jpg`;
					taken.push(new File([blob], name, { type: "image/jpeg" }));
					$strip.append(
						`<img src="${URL.createObjectURL(blob)}" style="width:64px;height:64px;
						 object-fit:cover;border-radius:4px;border:1px solid var(--border-color)">`
					);
					$hint.text(__("{0} photo(s) taken. Press Done when finished.", [taken.length]));
					if (!multiple) dialog.hide();
				}, "image/jpeg", 0.9);
			};

			dialog.$wrapper.on("hidden.bs.modal", () => {
				stop();
				if (settled) return;
				settled = true;
				resolve(taken);
			});

			navigator.mediaDevices
				.getUserMedia({ video: { facingMode: facing }, audio: false })
				.then((s) => {
					stream = s;
					video.srcObject = s;
					$hint.text(__("Frame the device and press Capture."));
					dialog.show();
				})
				.catch((err) => {
					stop();
					if (settled) return;
					settled = true;
					// NotAllowedError = the person said no; NotFoundError = no
					// camera on this machine. Both mean "use the file picker",
					// and the caller decides how to say so.
					reject({
						reason: err && err.name === "NotAllowedError" ? "denied" : "unavailable",
						message: err && err.message,
					});
				});
		});
	},
};
