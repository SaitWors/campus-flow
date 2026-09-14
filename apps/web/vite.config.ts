import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({plugins:[react()],server:{port:5173,proxy:{'/api/auth':'http://127.0.0.1:8101','/api/schedule':'http://127.0.0.1:8102','/api/queues':'http://127.0.0.1:8103','/api/notifications':'http://127.0.0.1:8104'}}});
