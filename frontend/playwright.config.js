import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  outputDir: '../test-results/browser',
  timeout: 45000,
  workers: 1,
  use: {
    baseURL: 'http://127.0.0.1:8000',
    channel: 'chrome',
    launchOptions: { args: ['--enable-unsafe-swiftshader'] },
    trace: 'retain-on-failure',
  },
});
