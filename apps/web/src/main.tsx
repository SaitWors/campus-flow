import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './styles.css';

try{document.documentElement.dataset.reducedMotion=localStorage.getItem('cf-reduce-motion')||'false';}catch{}

ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);

import './notifications.css';

import './pr3.css';
