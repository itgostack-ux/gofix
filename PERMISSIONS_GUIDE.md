# GoFix Permissions Guide

## ✅ Complete Setup - HRMS-Style Granular Permissions

All GoFix modules now have comprehensive role-based permissions configured, similar to HRMS module architecture.

---

## 📊 Permission Matrix

### Service Request
| Role | Create | Read | Write | Delete | Submit | Report | Export | Print |
|------|--------|------|-------|--------|--------|--------|--------|-------|
| System Manager | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Sales Manager | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Sales User | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ |
| Customer | ❌ | ✅ | ❌ | ❌ | ❌ | ✅ | ✅ | ✅ |

### Job Assignment
| Role | Create | Read | Write | Delete | Submit | Report | Export | Print |
|------|--------|------|-------|--------|--------|--------|--------|-------|
| System Manager | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Service Manager | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ |
| Service User | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ | ✅ | ✅ |
| Sales Manager | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ |
| Sales User | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ | ✅ | ✅ |

### Spare Parts Usage
| Role | Create | Read | Write | Delete | Submit | Report | Export | Print |
|------|--------|------|-------|--------|--------|--------|--------|-------|
| System Manager | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Service Manager | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ |
| Service User | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ | ✅ | ✅ |
| Sales Manager | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ | ✅ | ✅ |
| Sales User | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ | ✅ | ✅ |
| Stock User | ❌ | ✅ | ❌ | ❌ | ❌ | ✅ | ✅ | ✅ |

### Master Data (Walkin Source, Issue Category, Withdrawal Reason)
| Role | Create | Read | Write | Delete | Report | Export | Print |
|------|--------|------|-------|--------|--------|--------|-------|
| System Manager | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Sales Manager | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Sales User | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ |

### Job Tracker Page
**Accessible to:** System Manager, Sales Manager, Sales User

---

## 🎯 Role Descriptions

### System Manager
- **Full administrative access** to all GoFix modules
- Can create, modify, delete all records
- System configuration and setup permissions
- **Use case:** IT administrators, system implementers

### Sales Manager
- **Full operational access** to Service Requests and Master Data
- Can manage service operations, assignments, and spare parts
- Cannot delete Job Assignments or Spare Parts Usage (data integrity)
- **Use case:** Service center managers, operations heads

### Sales User
- **Day-to-day operational access** for creating and managing service requests
- Can create and edit records but cannot delete (except Service Requests)
- Limited access to master data (read/write but no delete)
- **Use case:** Front desk staff, service coordinators

### Service Manager
- **Specialized role** for service operations
- Full access to Job Assignments and Spare Parts Usage
- **Use case:** Workshop managers, service supervisors

### Service User
- **Technician-level access** to service operations
- Can view and edit assignments and spare parts but cannot delete
- **Use case:** Service engineers, technicians

### Customer
- **Read-only access** to their own Service Requests
- Can view, export, and print their service records
- **Use case:** End customers accessing portal

### Stock User
- **Read-only access** to Spare Parts Usage
- Can view inventory consumption for stock management
- **Use case:** Warehouse staff, inventory managers

---

## 🔧 How to Manage Permissions

### Option 1: Role Permission Manager (Recommended)
1. Navigate to: **Setup → Permissions → Role Permissions Manager**
2. Select DocType (e.g., "Service Request")
3. Click "Add a New Rule" to add roles
4. Configure permissions for each role
5. Save changes

**URL:** `http://localhost:8000/app/permission-manager`

### Option 2: Customize Form
1. Go to: **Setup → Customize → Customize Form**
2. Select DocType
3. Scroll to **Permissions** section
4. Add/Edit role permissions
5. Save and reload

### Option 3: Direct JSON Editing (Advanced)
Edit DocType JSON files in:
```
/home/palla/erpnext-bench/apps/gofix/gofix/gofix_services/doctype/<doctype_name>/
/home/palla/erpnext-bench/apps/gofix/gofix/masters/doctype/<doctype_name>/
```

After editing, run:
```bash
bench --site erpnext.local migrate
bench --site erpnext.local clear-cache
bench restart
```

---

## 🎓 Advanced Permission Features

### 1. User Permissions (Data Segregation)
Restrict users to see only specific records:
- **Setup → Permissions → User Permissions**
- Example: Restrict Sales User A to see only Customer X's service requests
- Example: Restrict Service Engineer to see only their assigned jobs

### 2. Permission Levels (Field-Level Security)
Hide/show specific fields based on role:
- In Customize Form, set **Perm Level** on fields
- Create permission rules for each perm level
- Example: Hide cost fields from Service User (perm level 1), show only to Sales Manager

### 3. Document Sharing
Allow users to share specific documents with others:
- Users with **Share** permission can share documents
- Recipients get temporary access even without role permissions

### 4. If Owner
Restrict users to see only records they created:
- In permission rule, check **If Owner** checkbox
- User can only access documents where they are the owner

---

## 📝 Common Permission Scenarios

### Scenario 1: New Technician Onboarding
**Goal:** Give technician access to assigned jobs only

**Steps:**
1. Create user with **Service User** role
2. In User Permissions, restrict to their Employee record
3. In Job Assignment, add condition: `assigned_user = user`

### Scenario 2: Branch-Wise Segregation
**Goal:** Separate service requests by branch

**Steps:**
1. Add **Branch** link field to Service Request
2. In User Permissions, assign users to their Branch
3. Service requests automatically filtered by branch

### Scenario 3: Customer Portal Access
**Goal:** Let customers view their service history

**Steps:**
1. Enable portal access for Customer role
2. Customer role already has read permissions on Service Request
3. Add condition: `customer = user.customer` (automatic in portal)

### Scenario 4: Manager Approval Workflow
**Goal:** Require manager approval for high-value repairs

**Steps:**
1. Create Workflow for Service Request
2. Add states: Draft → Pending Approval → Approved
3. Assign transitions: Sales User can submit, Sales Manager can approve
4. Permissions automatically follow workflow states

---

## 🔍 Troubleshooting Permissions

### "Not Permitted" Error
**Causes:**
1. User doesn't have required role
2. Link field points to DocType user can't access
3. User permission restricts the record

**Solutions:**
1. Assign appropriate role to user
2. Grant read permission on linked DocTypes
3. Check User Permissions settings

### "Cannot Delete" Error
**Causes:**
1. Role doesn't have delete permission
2. Document is submitted (need cancel permission)
3. Document has linked records

**Solutions:**
1. Use System Manager or Sales Manager role
2. Cancel document before deleting
3. Check if document is referenced elsewhere

### Changes Not Reflecting
**Causes:**
1. Migration not run
2. Cache not cleared
3. Browser cache not refreshed

**Solutions:**
```bash
bench --site erpnext.local migrate
bench --site erpnext.local clear-cache
bench restart
# In browser: Ctrl+Shift+R (hard refresh)
```

---

## 📌 Best Practices

1. **Principle of Least Privilege**
   - Give users minimum permissions needed for their job
   - Start restrictive, expand as needed

2. **Use Standard Roles First**
   - ERPNext has many built-in roles (Sales Manager, Sales User, etc.)
   - Create custom roles only if standard ones don't fit

3. **Document Your Permission Structure**
   - Keep a matrix of Role × DocType × Permissions
   - Update when making changes

4. **Test Permission Changes**
   - Create test users with different roles
   - Verify they can/cannot access what they should

5. **Regular Permission Audits**
   - Review who has access to what
   - Remove unnecessary permissions
   - Check for over-privileged users

6. **Use Workflows for Approvals**
   - Better than pure permissions for approval processes
   - Provides audit trail and notifications

---

## 🚀 Next Steps

Your GoFix module now has enterprise-grade permissions! You can:

1. **Access Role Permission Manager** to fine-tune permissions
2. **Set up User Permissions** for data segregation
3. **Create Workflows** for approval processes
4. **Configure field-level permissions** using Perm Levels
5. **Enable portal access** for customers

All GoFix DocTypes are now visible and manageable through ERPNext's standard permission tools, just like HRMS! 🎉

---

**Last Updated:** December 5, 2025  
**Version:** 1.0  
**Status:** ✅ Production Ready
