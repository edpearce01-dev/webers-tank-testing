// Weber's Tank Testing — v10.3 60fps Multi-Scene Screensaver Animation Engine
(function () {
  const INK = '#2b1911';
  let canvas = null, ctx = null, rafId = null;
  let W = 1920, H = 1080, DPR = 1;
  let SCENE_IDX = 0;
  let ACTIVE_SCENE = null;

  function ensureCanvas() {
    canvas = document.getElementById('tvAnimCanvas');
    if (!canvas) {
      canvas = document.createElement('canvas');
      canvas.id = 'tvAnimCanvas';
      canvas.style.cssText = 'position:fixed;inset:0;width:100vw;height:100vh;z-index:34;pointer-events:none;';
      document.body.appendChild(canvas);
    }
    ctx = canvas.getContext('2d');
    resizeCanvas();
  }

  function resizeCanvas() {
    if (!canvas) return;
    DPR = Math.min(window.devicePixelRatio || 1, 2);
    W = window.innerWidth || 1920;
    H = window.innerHeight || 1080;
    canvas.width = Math.round(W * DPR);
    canvas.height = Math.round(H * DPR);
    if (ctx) ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  }
  window.addEventListener('resize', resizeCanvas, { passive: true });

  function showSceneBadge(title) {
    let b = document.getElementById('tvSceneToast');
    if (!b) {
      b = document.createElement('div');
      b.id = 'tvSceneToast';
      b.style.cssText = 'position:fixed;top:18px;right:22px;z-index:40;background:rgba(12,38,27,0.88);color:#fde68a;border:1px solid #fbbf24;border-radius:999px;padding:5px 14px;font-family:var(--font-mono,monospace);font-size:0.74rem;font-weight:700;pointer-events:none;transition:opacity 0.35s;opacity:0;';
      document.body.appendChild(b);
    }
    b.textContent = title;
    b.style.opacity = '1';
    clearTimeout(b._tm);
    b._tm = setTimeout(() => { b.style.opacity = '0'; }, 2800);
  }

  // --- ARTICULATED 2-JOINTED GALLOPING DOG RENDERER ---
  function drawArticulatedLeg(g, hx, hy, hipAng, kneeAng, len1, len2, color, pawColor) {
    const kx = hx + Math.sin(hipAng) * len1;
    const ky = hy + Math.cos(hipAng) * len1;
    const fx = kx + Math.sin(hipAng + kneeAng) * len2;
    const fy = ky + Math.cos(hipAng + kneeAng) * len2;
    g.strokeStyle = INK;
    g.lineCap = 'round';
    g.lineJoin = 'round';
    g.lineWidth = 9.5;
    g.beginPath(); g.moveTo(hx, hy); g.lineTo(kx, ky); g.lineTo(fx, fy); g.stroke();
    g.strokeStyle = color;
    g.lineWidth = 6.2;
    g.beginPath(); g.moveTo(hx, hy); g.lineTo(kx, ky); g.lineTo(fx, fy); g.stroke();
    g.fillStyle = pawColor || color;
    g.strokeStyle = INK;
    g.lineWidth = 1.8;
    g.beginPath(); g.ellipse(fx + 2, fy, 4.8, 3.2, 0, 0, Math.PI * 2); g.fill(); g.stroke();
  }

  function drawGallopingDog(g, x, y, scale, phase, cfg, jumpPitch = 0, hasBall = false) {
    const bounce = -Math.abs(Math.sin(phase)) * 11;
    const pitch = jumpPitch || (Math.cos(phase) * 0.085);

    g.save();
    g.translate(x, y + bounce);
    g.scale(scale, scale);
    g.rotate(pitch);

    const fA1 = Math.sin(phase) * 0.68 + 0.12;
    const kA1 = -Math.max(0, Math.cos(phase)) * 0.85 - 0.15;
    const fA2 = Math.sin(phase + 0.55) * 0.68 + 0.12;
    const kA2 = -Math.max(0, Math.cos(phase + 0.55)) * 0.85 - 0.15;

    const rA1 = -Math.sin(phase + 0.3) * 0.72 - 0.12;
    const rk1 = Math.max(0, Math.cos(phase + 0.3)) * 0.78 + 0.18;
    const rA2 = -Math.sin(phase + 0.85) * 0.72 - 0.12;
    const rk2 = Math.max(0, Math.cos(phase + 0.85)) * 0.78 + 0.18;

    // Far-side legs (shaded slightly darker for depth)
    drawArticulatedLeg(g, -24, 6, rA2, rk2, 18, 17, cfg.shade, cfg.shade);
    drawArticulatedLeg(g, 22, 5, fA2, kA2, 18, 17, cfg.shade, cfg.shade);

    // Wagging / streaming tail
    const tailWag = Math.sin(phase * 2.2) * 0.28;
    g.save();
    g.translate(-34, -6);
    g.rotate(-0.45 + tailWag);
    g.strokeStyle = INK; g.lineWidth = cfg.bushy ? 10 : 6.5; g.lineCap = 'round';
    g.beginPath(); g.moveTo(0, 0); g.quadraticCurveTo(-16, -6, -26, -16); g.stroke();
    g.strokeStyle = cfg.coat; g.lineWidth = cfg.bushy ? 6.8 : 3.8;
    g.beginPath(); g.moveTo(0, 0); g.quadraticCurveTo(-16, -6, -26, -16); g.stroke();
    g.restore();

    // Torso & deep chest
    g.fillStyle = cfg.coat;
    g.strokeStyle = INK;
    g.lineWidth = 2.4;
    g.beginPath();
    g.ellipse(0, -1, 35, 16, -0.04, 0, Math.PI * 2);
    g.fill(); g.stroke();

    if (cfg.saddle) {
      g.fillStyle = cfg.saddle;
      g.beginPath();
      g.ellipse(-4, -6, 22, 9.5, -0.05, 0, Math.PI * 2);
      g.fill();
    }
    if (cfg.spots) {
      g.fillStyle = '#1e293b';
      [[-16, -4, 3.8], [-4, 2, 3.1], [8, -6, 4.2], [-22, 3, 2.8], [15, 2, 3.0]].forEach(([sx, sy, sr]) => {
        g.beginPath(); g.arc(sx, sy, sr, 0, Math.PI * 2); g.fill();
      });
    }

    // Near-side legs
    drawArticulatedLeg(g, -22, 7, rA1, rk1, 18, 17, cfg.coat, cfg.coat);
    drawArticulatedLeg(g, 24, 6, fA1, kA1, 18, 17, cfg.coat, cfg.coat);

    // Scarf / Bandana collar
    g.fillStyle = cfg.collar || '#dc2626';
    g.strokeStyle = INK; g.lineWidth = 2;
    g.beginPath();
    g.moveTo(19, -11); g.lineTo(31, -4); g.lineTo(22, 6); g.lineTo(13, -3); g.closePath();
    g.fill(); g.stroke();

    // Neck, Head & Snout
    g.save();
    g.translate(29, -14);
    g.fillStyle = cfg.coat;
    g.strokeStyle = INK; g.lineWidth = 2.3;
    g.beginPath();
    g.arc(0, 0, 12.5, 0, Math.PI * 2);
    g.fill(); g.stroke();

    // Snout
    g.beginPath();
    g.roundRect(7, -4, 15, 9.5, 4.5);
    g.fill(); g.stroke();
    g.fillStyle = '#111827';
    g.beginPath(); g.arc(21, -1, 2.8, 0, Math.PI * 2); g.fill();

    // Happy tongue OR caught tennis ball
    if (hasBall) {
      g.fillStyle = '#bef264'; g.strokeStyle = INK; g.lineWidth = 1.8;
      g.beginPath(); g.arc(17, 4, 6.2, 0, Math.PI * 2); g.fill(); g.stroke();
    } else {
      g.fillStyle = '#fb7185'; g.strokeStyle = INK; g.lineWidth = 1.4;
      g.beginPath();
      g.ellipse(13, 6.5 + Math.sin(phase * 2) * 1.2, 4.8, 2.6, 0.35, 0, Math.PI * 2);
      g.fill(); g.stroke();
    }

    // Eye
    g.fillStyle = '#111827';
    g.beginPath(); g.arc(5, -3.5, 2.1, 0, Math.PI * 2); g.fill();

    // Pointed Shepherd ears vs Flapping Dalmatian ear
    const earFlap = Math.sin(phase - 0.7) * 0.26;
    g.save();
    g.translate(-4, -8);
    g.rotate(earFlap);
    g.fillStyle = cfg.earColor || '#1e293b';
    g.strokeStyle = INK; g.lineWidth = 2;
    if (cfg.pointyEars) {
      g.beginPath(); g.moveTo(-3, 2); g.lineTo(1, -13); g.lineTo(7, 1); g.closePath(); g.fill(); g.stroke();
    } else {
      g.beginPath(); g.ellipse(-3, 5, 5.2, 9.5, 0.45, 0, Math.PI * 2); g.fill(); g.stroke();
    }
    g.restore();
    g.restore();
    g.restore();
  }

  // --- ARTICULATED ECONOMY VFD ENGINE #47 RENDERER (SPINNING SPOKED WHEELS + STROBES) ---
  function drawWheel(g, wx, wy, r, angle) {
    g.save();
    g.translate(wx, wy);
    g.fillStyle = '#1e293b'; g.strokeStyle = INK; g.lineWidth = 2.6;
    g.beginPath(); g.arc(0, 0, r, 0, Math.PI * 2); g.fill(); g.stroke();

    // Rotating tire tread notches + 6-spoke chrome rim
    g.rotate(angle);
    g.strokeStyle = '#475569'; g.lineWidth = 2;
    for (let i = 0; i < 8; i++) {
      const a = (i * Math.PI) / 4;
      g.beginPath(); g.moveTo(Math.cos(a) * (r - 4), Math.sin(a) * (r - 4)); g.lineTo(Math.cos(a) * r, Math.sin(a) * r); g.stroke();
    }
    g.fillStyle = '#e2e8f0'; g.strokeStyle = INK; g.lineWidth = 2;
    g.beginPath(); g.arc(0, 0, r * 0.56, 0, Math.PI * 2); g.fill(); g.stroke();

    g.strokeStyle = '#64748b'; g.lineWidth = 2.2;
    for (let i = 0; i < 6; i++) {
      const a = (i * Math.PI) / 3;
      g.beginPath(); g.moveTo(0, 0); g.lineTo(Math.cos(a) * (r * 0.52), Math.sin(a) * (r * 0.52)); g.stroke();
    }
    g.fillStyle = '#d97706';
    g.beginPath(); g.arc(0, 0, 4.5, 0, Math.PI * 2); g.fill();
    g.restore();
  }

  function drawEconomyFireEngine(g, x, y, t) {
    const susp = Math.sin(t * 20) * 1.8;
    const wheelR = 29;
    const wheelAng = x / wheelR;
    const strobeRed = Math.floor(t * 9) % 2 === 0;

    g.save();
    g.translate(x, y);

    // Sweeping warm headlight beam ahead of cab
    const beam = g.createLinearGradient(155, -28, 320, 10);
    beam.addColorStop(0, 'rgba(254, 240, 138, 0.50)');
    beam.addColorStop(1, 'rgba(254, 240, 138, 0)');
    g.fillStyle = beam;
    g.beginPath(); g.moveTo(155, -36); g.lineTo(325, -62); g.lineTo(325, 16); g.lineTo(155, -16); g.closePath(); g.fill();

    // Chassis body with suspension bounce
    g.save();
    g.translate(0, susp);

    // Rear pumper body
    g.fillStyle = '#c81e1e'; g.strokeStyle = INK; g.lineWidth = 2.8;
    g.beginPath(); g.roundRect(-155, -96, 195, 78, 7); g.fill(); g.stroke();

    // Cab forward
    g.fillStyle = '#dc2626';
    g.beginPath(); g.roundRect(36, -108, 116, 90, [6, 16, 5, 5]); g.fill(); g.stroke();

    // Gold reflective stripe + Economy VFD lettering
    g.fillStyle = '#fbbf24';
    g.fillRect(-153, -52, 303, 10);
    g.strokeRect(-153, -52, 303, 10);

    // Cab windows
    g.fillStyle = '#bae6fd';
    g.beginPath(); g.roundRect(50, -96, 44, 34, 4); g.fill(); g.stroke();
    g.beginPath(); g.roundRect(102, -96, 42, 34, 4); g.fill(); g.stroke();

    // Wooden/Aluminum Extension Ladder + Suction Hoses on rack
    g.fillStyle = '#fde68a';
    g.fillRect(-146, -110, 174, 9); g.strokeRect(-146, -110, 174, 9);
    for (let lx = -138; lx < 22; lx += 16) {
      g.beginPath(); g.moveTo(lx, -110); g.lineTo(lx, -101); g.stroke();
    }

    // Roll-up metal compartments & pump panel gauges
    g.fillStyle = '#cbd5e1';
    g.fillRect(-140, -86, 52, 30); g.strokeRect(-140, -86, 52, 30);
    g.fillRect(-26, -86, 52, 30); g.strokeRect(-26, -86, 52, 30);
    g.fillStyle = '#1e293b';
    g.fillRect(-78, -86, 42, 30); g.strokeRect(-78, -86, 42, 30);

    // Cab door badge text
    g.fillStyle = '#fef3c7';
    g.font = '800 11px "Barlow Condensed", sans-serif';
    g.textAlign = 'center';
    g.fillText('ECONOMY VFD', 94, -27);

    // Chrome bumpers & exhaust pipe
    g.fillStyle = '#f1f5f9';
    g.fillRect(-164, -22, 326, 9); g.strokeRect(-164, -22, 326, 9);

    // Flashing Emergency Roof Lightbars
    const glowColor = strobeRed ? 'rgba(239, 68, 68, 0.55)' : 'rgba(56, 189, 248, 0.50)';
    const rg = g.createRadialGradient(88, -116, 2, 88, -116, 48);
    rg.addColorStop(0, glowColor); rg.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = rg;
    g.beginPath(); g.arc(88, -116, 48, 0, Math.PI * 2); g.fill();

    g.fillStyle = strobeRed ? '#ef4444' : '#f8fafc';
    g.beginPath(); g.roundRect(74, -118, 28, 10, 4); g.fill(); g.stroke();
    g.fillStyle = !strobeRed ? '#ef4444' : '#fde047';
    g.beginPath(); g.roundRect(-142, -120, 20, 10, 4); g.fill(); g.stroke();

    g.restore(); // end chassis bounce

    // Independently rotating front & rear wheels
    drawWheel(g, -92, -12, wheelR, wheelAng);
    drawWheel(g, 96, -12, wheelR, wheelAng);
    g.restore();
  }

  const DOG_PONGO  = { coat: '#fffdf9', shade: '#cbd5e1', spots: true, collar: '#d97706', earColor: '#1e293b' };
  const DOG_ZEKE   = { coat: '#c2854c', shade: '#8c5828', saddle: '#29201b', bushy: true, pointyEars: true, collar: '#166534', earColor: '#29201b' };
  const DOG_PIERCE = { coat: '#fffdf9', shade: '#cbd5e1', spots: true, collar: '#dc2626', earColor: '#1e293b' };

  // =========================================================================
  // 5 MULTI-PART ANIMATED SCENES
  // =========================================================================
  function createFiretruckScene() {
    const puffs = [];
    const groundY = H * 0.80;
    const totalDur = 9.2;
    const dogsEl = document.getElementById('tvBgDogsImg');
    if (dogsEl) dogsEl.classList.add('chasing-hide');

    return {
      title: 'Scene 1/5 • Economy VFD Engine #47 Run',
      duration: totalDur,
      cleanup() { dogsEl?.classList.remove('chasing-hide'); },
      render(g, t, dt) {
        const truckX = -260 + ((W + 880) * (t / totalDur));
        if (Math.random() < 0.45) {
          puffs.push({ x: truckX - 158, y: groundY - 18, r: 6, a: 0.55, vx: -45 - Math.random() * 25, vy: -18 - Math.random() * 16 });
        }
        puffs.forEach(p => { p.x += p.vx * dt; p.y += p.vy * dt; p.r += 16 * dt; p.a -= 0.48 * dt; });
        for (let i = puffs.length - 1; i >= 0; i--) if (puffs[i].a <= 0) puffs.splice(i, 1);

        puffs.forEach(p => {
          g.fillStyle = `rgba(148, 163, 184, ${Math.max(0, p.a).toFixed(2)})`;
          g.beginPath(); g.arc(p.x, p.y, p.r, 0, Math.PI * 2); g.fill();
        });

        drawEconomyFireEngine(g, truckX, groundY, t);
        drawGallopingDog(g, truckX - 245, groundY - 22, 1.02, t * 15.5, DOG_PONGO);
        drawGallopingDog(g, truckX - 345, groundY - 26, 1.18, t * 14.2 + 1.4, DOG_ZEKE);
        drawGallopingDog(g, truckX - 445, groundY - 21, 0.98, t * 16.0 + 2.7, DOG_PIERCE);
      },
    };
  }

  function createTennisFetchScene() {
    const groundY = H * 0.81;
    const ball = { x: -30, y: groundY - 220, vx: W * 0.145, vy: -110, rot: 0, caught: false };
    const dust = [];
    return {
      title: 'Scene 2/5 • Shop Floor Tennis Ball Fetch',
      duration: 8.2,
      cleanup() {},
      render(g, t, dt) {
        const dogX = -140 + (W + 340) * (t / 7.6);
        let jumpY = 0, jumpPitch = 0;

        if (!ball.caught) {
          ball.vy += 560 * dt;
          ball.x += ball.vx * dt;
          ball.y += ball.vy * dt;
          ball.rot += 9 * dt;
          if (ball.y >= groundY - 10) {
            ball.y = groundY - 10;
            ball.vy = -Math.abs(ball.vy) * 0.72;
            for (let i = 0; i < 6; i++) {
              dust.push({ x: ball.x, y: groundY, vx: (Math.random() - 0.5) * 90, vy: -30 - Math.random() * 45, a: 0.7 });
            }
          }
          if (Math.abs(dogX + 38 - ball.x) < 42 && t > 2.2) {
            ball.caught = true;
          }
        }
        if (t > 1.9 && t < 3.1) {
          const jp = Math.sin(((t - 1.9) / 1.2) * Math.PI);
          jumpY = -jp * 68;
          jumpPitch = (t < 2.5 ? -0.25 : 0.22) * jp;
        }

        dust.forEach(d => { d.x += d.vx * dt; d.y += d.vy * dt; d.a -= 1.1 * dt; });
        for (let i = dust.length - 1; i >= 0; i--) if (dust[i].a <= 0) dust.splice(i, 1);
        dust.forEach(d => {
          g.fillStyle = `rgba(180, 155, 120, ${Math.max(0, d.a)})`;
          g.beginPath(); g.arc(d.x, d.y, 5, 0, Math.PI * 2); g.fill();
        });

        if (!ball.caught) {
          g.save();
          g.translate(ball.x, ball.y);
          g.rotate(ball.rot);
          g.fillStyle = '#bef264'; g.strokeStyle = INK; g.lineWidth = 2;
          g.beginPath(); g.arc(0, 0, 10, 0, Math.PI * 2); g.fill(); g.stroke();
          g.strokeStyle = '#ffffff'; g.lineWidth = 1.6;
          g.beginPath(); g.arc(-4, 0, 7, -0.9, 0.9); g.stroke();
          g.restore();
        }

        drawGallopingDog(g, dogX, groundY - 22 + jumpY, 1.08, t * 15.5, DOG_PONGO, jumpPitch, ball.caught);
      },
    };
  }

  function createHydroSightGlassScene() {
    const bubbles = Array.from({ length: 72 }, (_, i) => ({
      x: Math.random() * W,
      y: H + 30 + (i * 18),
      r: 4 + Math.random() * 13,
      vy: 75 + Math.random() * 85,
      wobble: Math.random() * Math.PI * 2,
    }));
    return {
      title: 'Scene 3/5 • 7,500 PSI Water-Jacket Verification',
      duration: 9.0,
      cleanup() {},
      render(g, t, dt) {
        bubbles.forEach(b => {
          b.y -= b.vy * dt;
          const bx = b.x + Math.sin(t * 2.6 + b.wobble) * 14;
          if (b.y > -30 && b.y < H + 30) {
            g.strokeStyle = 'rgba(14, 116, 144, 0.42)';
            g.fillStyle = 'rgba(224, 242, 254, 0.36)';
            g.lineWidth = 1.6;
            g.beginPath(); g.arc(bx, b.y, b.r, 0, Math.PI * 2); g.fill(); g.stroke();
            g.strokeStyle = 'rgba(255,255,255,0.85)';
            g.beginPath(); g.arc(bx - b.r * 0.28, b.y - b.r * 0.28, b.r * 0.35, Math.PI, Math.PI * 1.65); g.stroke();
          }
        });

        // Vintage Brass Master PID Gauge on bottom-right
        const alpha = t < 0.8 ? t / 0.8 : (t > 8.0 ? Math.max(0, (9.0 - t) / 1.0) : 1);
        const psiProg = t < 3.4 ? Math.min(1, t / 3.0) : (t < 6.2 ? 1 : Math.max(0, 1 - (t - 6.2) / 2.2));
        const psiVal = Math.round(psiProg * 7500);
        const gx = W - 155, gy = H - 145, gr = 76;

        g.save();
        g.globalAlpha = alpha;
        g.translate(gx, gy);
        g.fillStyle = '#d97706'; g.strokeStyle = INK; g.lineWidth = 3;
        g.beginPath(); g.arc(0, 0, gr + 8, 0, Math.PI * 2); g.fill(); g.stroke();
        g.fillStyle = '#fffefb';
        g.beginPath(); g.arc(0, 0, gr, 0, Math.PI * 2); g.fill(); g.stroke();

        // Green certified zone arc
        g.strokeStyle = '#10b981'; g.lineWidth = 7;
        g.beginPath(); g.arc(0, 0, gr - 12, -0.35, 0.45); g.stroke();

        for (let i = 0; i <= 10; i++) {
          const a = Math.PI * 0.75 + (i / 10) * Math.PI * 1.5;
          g.strokeStyle = INK; g.lineWidth = i % 2 === 0 ? 2.4 : 1.2;
          g.beginPath();
          g.moveTo(Math.cos(a) * (gr - 18), Math.sin(a) * (gr - 18));
          g.lineTo(Math.cos(a) * (gr - 6), Math.sin(a) * (gr - 6));
          g.stroke();
        }

        g.fillStyle = '#0c261b';
        g.font = '700 12px "JetBrains Mono", monospace';
        g.textAlign = 'center';
        g.fillText(`${psiVal} PSI`, 0, 32);
        if (psiProg > 0.96) {
          g.fillStyle = '#059669';
          g.font = '800 11px "JetBrains Mono", monospace';
          g.fillText('B386 PASS ✓', 0, 49);
        }

        const needleAng = Math.PI * 0.75 + (psiProg * 0.75) * Math.PI * 1.5;
        g.rotate(needleAng);
        g.strokeStyle = '#dc2626'; g.lineWidth = 3.2; g.lineCap = 'round';
        g.beginPath(); g.moveTo(-12, 0); g.lineTo(gr - 16, 0); g.stroke();
        g.fillStyle = INK;
        g.beginPath(); g.arc(0, 0, 6, 0, Math.PI * 2); g.fill();
        g.restore();
      },
    };
  }

  function createSeasonalSignatureScene() {
    const hKey = window.ACTIVE_HOLIDAY_KEY || 'THANKSGIVING';

    if (hKey === 'CHRISTMAS') {
      const sparks = [];
      return {
        title: 'Scene 4/5 • Christmas Sleigh Flyover',
        duration: 8.8,
        cleanup() {},
        render(g, t, dt) {
          const sx = -220 + (W + 460) * (t / 8.5);
          const sy = H * 0.22 + Math.sin(t * 2.1) * 28;
          if (Math.random() < 0.7) {
            sparks.push({ x: sx - 40, y: sy + 10, vy: 28 + Math.random() * 30, vx: -20 + Math.random() * 12, a: 1 });
          }
          sparks.forEach(s => { s.x += s.vx * dt; s.y += s.vy * dt; s.a -= 0.45 * dt; });
          sparks.filter(s => s.a > 0).forEach(s => {
            g.fillStyle = `rgba(251, 191, 36, ${s.a.toFixed(2)})`;
            g.beginPath(); g.arc(s.x, s.y, 3.2, 0, Math.PI * 2); g.fill();
          });

          // Sleigh + 2 bounding reindeer in warm dark-forest & gold silhouette
          g.save();
          g.translate(sx, sy);
          g.fillStyle = '#0c261b'; g.strokeStyle = '#fbbf24'; g.lineWidth = 2;
          g.beginPath(); g.roundRect(-75, -18, 66, 28, 8); g.fill(); g.stroke();
          [28, 92].forEach((rx, idx) => {
            const hop = Math.sin(t * 12 + idx * 1.4) * 8;
            g.beginPath(); g.ellipse(rx, -6 + hop, 20, 9, 0, 0, Math.PI * 2); g.fill(); g.stroke();
            g.beginPath(); g.arc(rx + 18, -16 + hop, 7, 0, Math.PI * 2); g.fill();
            if (idx === 1) { g.fillStyle = '#ef4444'; g.beginPath(); g.arc(rx + 25, -16 + hop, 3.5, 0, Math.PI * 2); g.fill(); g.fillStyle = '#0c261b'; }
          });
          g.strokeStyle = '#fbbf24'; g.beginPath(); g.moveTo(-10, -8); g.lineTo(92, -6); g.stroke();
          g.restore();
        },
      };
    }

    if (hKey === 'NEW_YEAR' || hKey === 'JULY_4TH') {
      const shells = [0.4, 1.8, 3.2, 4.6, 6.0].map((t0, i) => ({
        t0, bx: W * (0.2 + i * 0.15), by: H * (0.22 + (i % 2) * 0.1),
        exploded: false, sparks: [],
      }));
      const cols = ['#fbbf24', '#ef4444', '#34d399', '#60a5fa', '#fde047'];
      return {
        title: 'Scene 4/5 • Celebration Fireworks',
        duration: 8.6,
        cleanup() {},
        render(g, t, dt) {
          shells.forEach((sh, idx) => {
            if (t >= sh.t0 && !sh.exploded) {
              sh.exploded = true;
              const col = cols[idx % cols.length];
              for (let k = 0; k < 46; k++) {
                const a = (k / 46) * Math.PI * 2;
                const sp = 65 + Math.random() * 135;
                sh.sparks.push({ x: sh.bx, y: sh.by, vx: Math.cos(a) * sp, vy: Math.sin(a) * sp, a: 1, col });
              }
            }
            sh.sparks.forEach(p => {
              p.vy += 95 * dt; p.x += p.vx * dt; p.y += p.vy * dt; p.a -= 0.42 * dt;
              if (p.a > 0) {
                g.globalAlpha = Math.max(0, p.a);
                g.fillStyle = p.col;
                g.beginPath(); g.arc(p.x, p.y, 3.4, 0, Math.PI * 2); g.fill();
              }
            });
          });
          g.globalAlpha = 1;
        },
      };
    }

    // Default / Thanksgiving: Plump hand-inked Wild Turkey Trot chased by Pongo!
    const groundY = H * 0.81;
    const dogsEl = document.getElementById('tvBgDogsImg');
    dogsEl?.classList.add('chasing-hide');
    return {
      title: 'Scene 4/5 • Baden Wild Turkey Trot',
      duration: 8.4,
      cleanup() { dogsEl?.classList.remove('chasing-hide'); },
      render(g, t) {
        const tx = -120 + (W + 460) * (t / 8.2);
        const bob = Math.abs(Math.sin(t * 18)) * 8;
        g.save();
        g.translate(tx, groundY - 22 - bob);

        // Fan tail feathers
        ['#b45309', '#dc2626', '#d97706', '#92400e', '#f59e0b'].forEach((fc, i) => {
          g.save();
          g.rotate(-0.9 + i * 0.24);
          g.fillStyle = fc; g.strokeStyle = INK; g.lineWidth = 2;
          g.beginPath(); g.ellipse(-26, -6, 18, 8, 0, 0, Math.PI * 2); g.fill(); g.stroke();
          g.restore();
        });

        // Fast-cycling turkey legs
        [0, Math.PI].forEach(off => {
          const lx = Math.sin(t * 22 + off) * 12;
          const ly = 16 + Math.cos(t * 22 + off) * 5;
          g.strokeStyle = '#d97706'; g.lineWidth = 3.5;
          g.beginPath(); g.moveTo(0, 8); g.lineTo(lx, ly); g.stroke();
        });

        // Plump body + neck + red wattle
        g.fillStyle = '#78350f'; g.strokeStyle = INK; g.lineWidth = 2.4;
        g.beginPath(); g.ellipse(0, -2, 24, 19, 0, 0, Math.PI * 2); g.fill(); g.stroke();
        g.fillStyle = '#9a3412';
        g.beginPath(); g.arc(22, -18, 9, 0, Math.PI * 2); g.fill(); g.stroke();
        g.fillStyle = '#f59e0b';
        g.beginPath(); g.moveTo(30, -20); g.lineTo(40, -16); g.lineTo(30, -13); g.closePath(); g.fill(); g.stroke();
        g.fillStyle = '#dc2626';
        g.beginPath(); g.ellipse(29, -11, 3.5, 6, 0.2, 0, Math.PI * 2); g.fill();
        g.fillStyle = '#111827';
        g.beginPath(); g.arc(24, -20, 2, 0, Math.PI * 2); g.fill();
        g.restore();

        drawGallopingDog(g, tx - 170, groundY - 22, 1.0, t * 15.8, DOG_PONGO);
        drawGallopingDog(g, tx - 265, groundY - 21, 0.96, t * 16.2 + 1.6, DOG_PIERCE);
      },
    };
  }

  function createAmbientButterfliesOrFirefliesScene() {
    const isNight = new Date().getHours() >= 18 || new Date().getHours() < 6;
    const dogsEl = document.getElementById('tvBgDogsImg');

    if (isNight) {
      const bugs = Array.from({ length: 44 }, () => ({
        x: Math.random() * W, y: Math.random() * H * 0.85,
        vx: (Math.random() - 0.5) * 28, vy: (Math.random() - 0.5) * 20,
        ph: Math.random() * Math.PI * 2,
      }));
      return {
        title: 'Scene 5/5 • Evening Beaver Valley Fireflies',
        duration: 9.0,
        cleanup() {},
        render(g, t, dt) {
          bugs.forEach(b => {
            b.x += b.vx * dt; b.y += b.vy * dt;
            const pulse = 0.25 + 0.75 * Math.pow(0.5 + 0.5 * Math.sin(t * 3.2 + b.ph), 2);
            const rg = g.createRadialGradient(b.x, b.y, 1, b.x, b.y, 16);
            rg.addColorStop(0, `rgba(253, 224, 71, ${pulse.toFixed(2)})`);
            rg.addColorStop(1, 'rgba(253, 224, 71, 0)');
            g.fillStyle = rg;
            g.beginPath(); g.arc(b.x, b.y, 16, 0, Math.PI * 2); g.fill();
          });
        },
      };
    }

    dogsEl?.classList.add('trick-tilt');
    return {
      title: 'Scene 5/5 • Shop Meadow Butterflies',
      duration: 8.5,
      cleanup() { dogsEl?.classList.remove('trick-tilt'); },
      render(g, t) {
        [0, 1].forEach(idx => {
          const prog = (t + idx * 0.9) / 8.5;
          const bx = W * 0.1 + prog * W * 0.82;
          const by = H * (0.36 + idx * 0.08) + Math.sin(t * 2.4 + idx * 2) * 55;
          const flap = Math.sin(t * 17 + idx) * 0.85;
          g.save();
          g.translate(bx, by);
          g.rotate(0.18 * Math.cos(t * 2.4));
          g.scale(1, Math.max(0.18, Math.abs(flap)));
          g.fillStyle = idx === 0 ? '#f97316' : '#facc15';
          g.strokeStyle = INK; g.lineWidth = 2;
          g.beginPath(); g.ellipse(-7, -8, 11, 14, -0.35, 0, Math.PI * 2); g.fill(); g.stroke();
          g.beginPath(); g.ellipse(7, -8, 9, 12, 0.35, 0, Math.PI * 2); g.fill(); g.stroke();
          g.restore();
        });
      },
    };
  }

  const SCENE_BUILDERS = [
    createFiretruckScene,
    createTennisFetchScene,
    createHydroSightGlassScene,
    createSeasonalSignatureScene,
    createAmbientButterfliesOrFirefliesScene,
  ];

  function stopActiveScene() {
    if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
    if (ACTIVE_SCENE && ACTIVE_SCENE.cleanup) ACTIVE_SCENE.cleanup();
    ACTIVE_SCENE = null;
    if (ctx && canvas) ctx.clearRect(0, 0, W, H);
  }

  function playSceneIndex(idx, showBadge = false) {
    ensureCanvas();
    stopActiveScene();
    const builder = SCENE_BUILDERS[idx % SCENE_BUILDERS.length];
    const scene = builder();
    ACTIVE_SCENE = scene;
    if (showBadge) showSceneBadge(scene.title);

    let t0 = performance.now();
    let prev = t0;

    function frame(now) {
      if (!document.body.classList.contains('tv-screensaver')) {
        stopActiveScene();
        return;
      }
      const elapsed = (now - t0) / 1000;
      const dt = Math.min(0.05, (now - prev) / 1000);
      prev = now;
      ctx.clearRect(0, 0, W, H);
      scene.render(ctx, elapsed, dt);
      if (elapsed < scene.duration) {
        rafId = requestAnimationFrame(frame);
      } else {
        stopActiveScene();
      }
    }
    rafId = requestAnimationFrame(frame);
  }

  window.stopAllTvScenes = stopActiveScene;

  window.triggerNextTvScene = function (manualPreview = false) {
    if (manualPreview && typeof window.enterScreensaver === 'function' && !document.body.classList.contains('tv-screensaver')) {
      window.enterScreensaver(true);
    }
    if (!document.body.classList.contains('tv-screensaver')) return;
    const pick = SCENE_IDX % SCENE_BUILDERS.length;
    SCENE_IDX++;
    playSceneIndex(pick, manualPreview);
  };

  // Automatically play the next animated scene every 55 seconds while in Screensaver mode!
  setInterval(() => {
    if (document.body.classList.contains('tv-screensaver') && !ACTIVE_SCENE) {
      window.triggerNextTvScene(false);
    }
  }, 55000);
})();
