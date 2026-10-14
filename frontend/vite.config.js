import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const renderHostname = process.env.RENDER_EXTERNAL_HOSTNAME;

export default defineConfig({
  plugins: [react()],
  preview: {
    host: '0.0.0.0',
    port: Number(process.env.PORT) || 4173,
    allowedHosts: renderHostname ? [renderHostname] : ['.onrender.com'],
  },
});
