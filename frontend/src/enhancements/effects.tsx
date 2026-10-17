import { Particles, ParticlesProvider, useParticlesProvider } from "@tsparticles/react";
import { loadSlim } from "@tsparticles/slim";
import { animate } from "motion";
import { useEffect, useRef } from "react";

const prefersReducedMotion = () =>
  typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const RainCanvas = () => {
  const { loaded } = useParticlesProvider();
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!loaded || !root.current || prefersReducedMotion()) {
      return undefined;
    }
    const animation = animate(root.current, { opacity: [0, 1] }, { duration: 0.2, ease: "easeOut" });
    return () => animation.stop();
  }, [loaded]);

  if (!loaded) {
    return null;
  }
  return (
    <div ref={root} className="atmosphere-layer atmosphere-layer--rain" data-testid="rain-atmosphere" aria-hidden="true">
      <Particles
        id="nining-rain-atmosphere"
        className="atmosphere-layer__particles"
        options={{
          fullScreen: { enable: false },
          detectRetina: false,
          fpsLimit: 24,
          particles: {
            color: { value: "#2E6F95" },
            move: { enable: true, speed: 1.6, direction: "bottom", straight: true, outModes: { default: "out" } },
            number: { value: 72, density: { enable: true, width: 900, height: 700 } },
            opacity: { value: { min: 0.22, max: 0.42 } },
            shape: { type: "circle" },
            size: { value: { min: 1, max: 3 } },
          },
        }}
      />
    </div>
  );
};

/** Optional atmosphere: this chunk is loaded only after the player turns it on. */
const EffectsLayer = () => (
  <ParticlesProvider init={loadSlim}>
    <RainCanvas />
  </ParticlesProvider>
);

let finalCelebrationUsed = false;

/**
 * The app currently has no strict final gate, so nothing calls this function.
 * If such a backend gate is introduced, this function permits one small,
 * reduced-motion-aware celebration only after that exact gate is true.
 */
export const runFinalGateConfettiOnce = (host: HTMLElement, finalGatePassed: boolean): boolean => {
  if (!finalGatePassed || finalCelebrationUsed || prefersReducedMotion()) {
    return false;
  }
  finalCelebrationUsed = true;
  host.classList.add("final-gate--celebrate");
  const animation = animate(host, { opacity: [0.92, 1] }, { duration: 0.12 });
  animation.then(() => host.classList.remove("final-gate--celebrate"));
  return true;
};

export default EffectsLayer;
