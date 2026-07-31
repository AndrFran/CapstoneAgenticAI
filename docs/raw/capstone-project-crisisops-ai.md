# Building an Intelligent Supply Chain Disruption Management Platform

> Converted from `Capstone Project _ CrisisOps AI (2).pdf` (11 pages).

---

## Overview

Your team at **GlobalLogic** has been hired by **NovaRetail Group**, a multinational retail and distribution organization, to build an AI-powered supply chain operations assistant.

NovaRetail manages suppliers, warehouses, shipments, inventory, and customer orders through multiple systems. When a shipment is delayed, inventory becomes unavailable, or a supplier fails to deliver, operations teams must manually check several portals and coordinate with different departments.

NovaRetail wants a single intelligent platform capable of understanding supply chain incidents, identifying the affected business area, invoking the appropriate operational tools, and coordinating recovery workflows.

The client expects a production-ready MVP within **two days**.

You will work as an AI consulting team, where each member is responsible for a different part of the solution.

---

## Client Profile

### Organization

**NovaRetail Group**

- 150 Retail Stores
- 8 Regional Warehouses
- 300+ Suppliers
- Nationwide Delivery Network
- Central Supply Chain Team
- 5,000 Daily Shipments

### Existing Technology

- Supplier Management System
- Warehouse Management System
- Inventory Management System
- Shipment Tracking Platform
- Purchase Order System
- Customer Order Portal
- Notification System
- Email and Teams

All systems expose mock REST APIs or JSON data for this capstone. It will be provided with the project.

---

## Current Business Challenges

Supply chain employees currently use multiple systems.

Examples:

- Track Shipments
- Check Inventory
- Find Supplier Details
- Identify Delayed Orders
- Report Damaged Goods
- Find Alternative Suppliers
- Create Supply Chain Incidents

Operations teams want one AI assistant instead of navigating several separate systems.

---

## Executive Goals

The COO expects:

- Unified Operations Experience
- Intelligent Incident Routing
- Faster Disruption Response
- Reduced Manual Coordination
- Better Supply Chain Visibility
- AI-Assisted Recovery Planning

---

## Project Objective

Build an AI-powered Supply Chain Assistant capable of understanding operational requests, deciding which supply chain function should handle them, executing the required workflow, and responding naturally.

---

## Team Structure and Role

Students should work in teams of **4 members**.

---

## Team Member 1

### Request Intake & Incident Analysis Agent Engineer

Responsible for:

- Chat UI
- Prompt Engineering
- Conversation Memory
- Conversation History
- System Prompt
- Request Intake Agent
- Incident Analysis Agent

### Request Intake Agent

Handles:

- Understanding supply chain requests
- Identifying incident type
- Extracting shipment, supplier, product, and warehouse details
- Detecting missing information
- Preparing the request for routing

### Incident Analysis Agent

Handles:

- Shipment Delays
- Supplier Failures
- Inventory Shortages
- Route Disruptions
- Damaged Goods Reports
- Incident Severity Classification

### Example Tools

```
get_shipment_details()
get_supplier_details()
check_route_status()
classify_incident_severity()
```

---

## Team Member 2

### Shipment & Order Impact Agent Engineer

Responsible for:

- Shipment-related prompt design
- Shipment and order tool development
- JSON or mock API integration
- Shipment Agent development
- Agent testing
- Error handling for shipment workflows

### Shipment Agent

Handles:

- Track Shipment
- Check Shipment Delay
- Retrieve Shipment Details
- Identify Affected Orders
- Check Delivery Route
- Estimate Delivery Impact

### Example Tools

```
track_shipment()
get_shipment_status()
find_affected_orders()
check_delivery_route()
estimate_delivery_delay()
```

---

## Team Member 3

### Inventory & Supplier Agent Engineer

Responsible for:

- Inventory and supplier prompt design
- Inventory and supplier tool development
- Mock API or JSON integration
- Inventory Agent development
- Supplier Agent development
- Agent testing

### Inventory Agent

Handles:

- Check Product Stock
- Identify Inventory Shortages
- Check Warehouse Availability
- Calculate Required Quantity
- Find Transfer Options

### Supplier Agent

Handles:

- Find Supplier Details
- Check Supplier Availability
- Find Alternative Suppliers
- Compare Supplier Options
- Estimate Procurement Cost

### Example Tools

```
check_inventory()
check_warehouse_stock()
find_inventory_transfer()
search_supplier()
find_alternative_supplier()
compare_supplier_options()
```

---

## Team Member 4

### Recovery & Supervisor Agent Engineer

Responsible for:

- Recovery Agent
- Supervisor Agent
- LangGraph Workflow
- Agent Routing
- Shared State
- Human-in-the-loop
- Final Response Generation
- Cross-agent error handling

### Recovery Agent

Handles:

- Recovery Planning
- Alternative Supplier Recommendations
- Inventory Transfer Recommendations
- Shipment Rerouting Recommendations
- Incident Escalation
- Stakeholder Summary

### Example Tools

```
generate_recovery_plan()
estimate_recovery_cost()
reroute_shipment()
create_incident()
generate_incident_summary()
```

### Supervisor Agent

Receives every request and determines whether it belongs to:

- Incident Analysis
- Shipment
- Inventory
- Supplier
- Recovery

The Supervisor Agent should:

- Route requests to the correct agent
- Coordinate multiple agents when required
- Maintain shared workflow state
- Handle unsupported requests
- Combine agent outputs
- Generate the final response

---

## Shared Responsibilities

All four team members are responsible for:

- LangSmith Tracing
- Prompt Evaluation
- Testing
- Deployment
- Documentation
- Architecture Diagram
- Final Presentation

No single team member should be solely responsible for evaluation or deployment.

---

## Functional Requirements

The assistant should support requests like:

### Shipment

- Track Shipment
- Check Shipment Delay
- Identify Affected Orders

### Inventory

- Check Product Stock
- Identify Inventory Shortage
- Check Warehouse Availability

### Supplier

- Find Supplier Details
- Check Supplier Availability
- Find Alternative Suppliers

### Incident Management

- Create Supply Chain Incident
- Check Incident Status
- Escalate Critical Incident

### Recovery Planning

- Recommend Recovery Action
- Compare Supplier Alternatives
- Generate Incident Summary

---

## LangSmith Requirements

Every team must:

- Enable tracing
- Record at least 10 conversations
- Analyze latency
- Review failed runs
- Improve one prompt based on trace insights

---

## Deployment

Deploy the application using:

- Streamlit
- Environment Variables
- Production-ready README

---

## Deliverables

Each team must submit:

### 1. Working Application

Fully functional Streamlit application.

### 2. Source Code

Well-structured repository with proper folder organization.

### 3. Architecture Diagram

Illustrate:

- LLM
- Tools
- LangGraph Workflow
- Multi-Agent Design

### 4. LangSmith Report

Include:

- Traces
- Prompt improvements
- Evaluation observations

### 5. Deployment

Live URL using a free cloud service such as Streamlit Community Cloud, or a local deployment demonstration if internet access is unavailable.

### 6. Technical Presentation

Include:

- Project Overview
- Setup Instructions
- Folder Structure
- Team Responsibilities
- Future Enhancements

---

## Evaluation Criteria

| Criteria | Weight (50) |
|---|---|
| LangChain & Tool Integration | 10 |
| Agent, Multi-Agent Collaboration & LangGraph Workflow | 15 |
| LangSmith Tracing, Evaluation & Deployment | 10 |
| Team Collaboration & Final Presentation | 15 |
