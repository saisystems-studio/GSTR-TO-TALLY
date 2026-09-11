import React from 'react'; import {createRoot} from 'react-dom/client'; import App from './App.jsx';
import SuperAdminApp from './superadmin/SuperAdminApp.jsx';
import './styles/compact-invoice.css';
const Root = window.location.pathname.startsWith('/superadmin') ? SuperAdminApp : App;
createRoot(document.getElementById('root')).render(<React.StrictMode><Root/></React.StrictMode>)
