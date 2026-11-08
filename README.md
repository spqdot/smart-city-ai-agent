# 🚍 Smart City AI Agent – Lisbon Journey Planner

A Smart City AI-powered public transport journey planning application designed to simplify travel across **Lisbon, Almada, and Setúbal**.

Built with React, TypeScript, and FastAPI, the application provides timetable-based journey planning, multi-modal transport route discovery, and interactive map visualization. The full-stack application is deployed on **Microsoft Azure**, with automated CI/CD workflows using GitHub Actions.

## 🚀 Live Application

Explore the deployed application:

| Component | Live Link |
|---|---|
| **Frontend – Azure Static Web Apps** | [Open Lisbon Journey Planner](https://mango-dune-02f8d2603.3.azurestaticapps.net/) |
| **Frontend – Alternative Link** | [Open Live Demo](https://lnkd.in/eMuRe4Mr) |
| **Backend – Azure Container Apps** | [Backend API](https://lnkd.in/eeKtxDf7) |
| **Backend API Documentation** | [Explore API Documentation](https://lnkd.in/eeKtxDf7) |
| **GitHub Repository** | [Smart City AI Agent](https://github.com/spqdot/smart-city-ai-agent) |

## 🌆 Project Overview

Lisbon Journey Planner is a Smart City transport application that helps users discover public transport journeys across Lisbon, Almada, and Setúbal.

The application connects a React frontend with a FastAPI backend to provide scheduled public transport journey planning. Users can search for stops and destinations, explore available connections, and visualize selected itineraries on an interactive map.

The system supports multi-modal journey planning by combining different public transport services, including metro, trains, buses, and other transport options.

## ✨ Key Features

### 1. Public Transport Stop Search
- Search for public transport stops and destinations.
- Explore transport connections across Lisbon, Almada, and Setúbal.

### 2. Timetable-Based Journey Planning
- Find scheduled journeys for the next two hours.
- Retrieve available public transport connections.
- Explore alternative journey options.

### 3. Multi-Modal Route Planning
- Combine metro, train, bus, and other transport services.
- Discover connections involving multiple transport modes.
- Support regional public transport journey planning.

### 4. Interactive Map Visualization
- Visualize selected journey itineraries.
- Explore transport routes using interactive maps.
- Use Leaflet and OpenStreetMap for map visualization.

## 🏗️ System Architecture

The application follows a modular full-stack architecture.

```text
                 User
                  |
                  v
          React Frontend
         TypeScript + Vite
                  |
                  v
           REST API Calls
                  |
                  v
          FastAPI Backend
                  |
                  v
        Journey Planning Logic
                  |
                  v
       Transport Timetable Data
                  |
                  v
        Journey Results / Routes
                  |
                  v
        Leaflet Interactive Map
          OpenStreetMap
```

## 🛠️ Technology Stack

| Area | Technologies |
|---|---|
| Frontend | React, TypeScript, Vite |
| Backend | Python, FastAPI |
| Cloud Platform | Microsoft Azure |
| Frontend Hosting | Azure Static Web Apps |
| Backend Hosting | Azure Container Apps |
| Container Registry | Azure Container Registry (ACR) |
| CI/CD | GitHub Actions |
| Mapping | Leaflet, OpenStreetMap |
| Version Control | Git, GitHub |

## ☁️ Microsoft Azure Deployment

The application is deployed on Microsoft Azure, with separate hosting services for the frontend and backend.

**Deployment components:**

- **Azure Static Web Apps:** Hosts the React frontend and provides public access to the journey planning application.
- **Azure Container Apps:** Runs the containerized FastAPI backend.
- **Azure Container Registry (ACR):** Stores and manages backend Docker container images.
- **GitHub Actions:** Automates the CI/CD workflow for application deployment.

The frontend communicates with the backend through REST API requests, enabling users to access the application through its public Azure deployment.

### Deployment Architecture

```text
             GitHub Repository
                    |
                    v
             GitHub Actions
               CI / CD
                    |
          +---------+---------+
          |                   |
          v                   v
   Azure Static Web     Azure Container
       Apps                  Registry
          |                   |
          v                   v
   React Frontend       Docker Image
                              |
                              v
                       Azure Container
                           Apps
                              |
                              v
                         FastAPI
                          Backend
```

## 💻 Run Locally

### Prerequisites

- Python
- Node.js and npm
- Git

### 1. Clone the Repository

```bash
git clone https://github.com/spqdot/smart-city-ai-agent.git
cd smart-city-ai-agent
```

### 2. Backend Setup

Install the backend dependencies according to the project configuration.

Start the FastAPI application:

```bash
uvicorn main:app --reload
```

Backend URL:

```text
http://localhost:8000
```

API documentation:

```text
http://localhost:8000/docs
```

### 3. Frontend Setup

Open a separate terminal:

```bash
cd frontend
npm install
npm run dev
```

Open the local development URL displayed by Vite.

## 📚 Key Learning Outcomes

This project provided practical experience in:

- Developing a full-stack application using React and FastAPI.
- Designing and integrating REST APIs.
- Implementing timetable-based public transport journey planning.
- Supporting multi-modal transport route discovery.
- Integrating interactive maps using Leaflet and OpenStreetMap.
- Containerizing backend applications.
- Deploying frontend and backend services independently on Microsoft Azure.
- Managing Docker images using Azure Container Registry.
- Automating CI/CD workflows with GitHub Actions.
- Building and deploying a cloud-hosted application.

## 🔮 Future Enhancements

- Integration with additional real-time public transport data sources.
- Improved journey optimization and route recommendations.
- Real-time transport disruption notifications.
- Enhanced accessibility and mobility options.
- AI-powered personalized journey recommendations.

## 👩‍💻 Author

**Shrabani Panigrahi**

AI Engineer | Generative AI | Python | Full-Stack AI Applications | Cloud Deployment

- [GitHub Profile](https://github.com/spqdot)
- [Project Repository](https://github.com/spqdot/smart-city-ai-agent)

---

*Building practical technology solutions that connect software engineering, cloud computing, and real-world urban mobility challenges.*

**Technologies:** `Python` `FastAPI` `React` `TypeScript` `Azure` `Docker` `GitHub Actions` `Leaflet` `OpenStreetMap`