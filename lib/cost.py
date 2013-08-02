#! /usr/bin/env python

from molmod.units import kjmol

import numpy as np

from quickff.tools import matrix_squared_sum
from scipy.optimize import minimize

__all__ = ['HessianFCCost']

class HessianFCCost(object):
    '''
        A least squares cost function measuring the deviation of the force
        field hessian from the ab initio hessian. Only the force constants
        are regarded as variable parameters, the rest values are kept fixed.
        The initial force constants and fixed rest values should be stored
        in attributes of the members of model.vterms.

        The cost function measures half the sum of the squares of the
        difference between ab initio and force field hessian elements:

            chi^2 = 0.5*sum( (H^ai_ij - H^ff_ij)^2 , ij=...)

        This cost function can be rewritten as:

            chi^2(k) = 0.5*np.dot(k.T, np.dot(A, k)) - np.dot(B.T, k) + 0.5*C

        in which k is the vector of unknown force constants.
    '''
    def __init__(self, system, model):
        '''
            **Arguments*

            system
                An instance of the System class containing all system info

            model
                An instance of the Model class defining the total energy,
                the electrostatic contribution and the valence terms.
        '''
        self.system = system
        self.model = model
        self._A = np.zeros([model.val.nterms, model.val.nterms], float)
        self._B = np.zeros([model.val.nterms], float)
        self._C = 0.0

    def _update_lstsq_matrices(self):
        '''
            Calculate the matrix A, vector B and scalar C of the cost function
        '''
        ndofs = 3*self.system.natoms
        ref  = self.system.ref.hess.reshape([ndofs, ndofs])
        ref -= self.model.ei.calc_hessian(self.system.ref.coords).reshape([ndofs, ndofs])
        self._A = np.zeros([self.model.val.nterms, self.model.val.nterms], float)
        self._B = np.zeros([self.model.val.nterms], float)
        h = np.zeros([self.model.val.nterms, ndofs, ndofs], float)
        for i, icname in enumerate(sorted(self.system.ics.keys())):
            for vterm in self.model.val.vterms[icname]:
                h[i] += vterm.calc_hessian(coords=self.system.ref.coords, k=1.0)
            self._B[i] = matrix_squared_sum(h[i], ref)
            for j in xrange(i+1):
                self._A[i, j] = matrix_squared_sum(h[i], h[j])
                self._A[j, i] = self._A[i, j]
        self._C = matrix_squared_sum(ref, ref)

    def _define_constraints(self, fixed, kinit):
        'Define the constraints active during the minimization'
        constraints = []
        for i in xrange(self.model.val.nterms):
            icname = sorted(self.model.val.vterms.keys())[i]
            if fixed is not None and icname.split('/')[0] in fixed:
                constraints.append(
                    FixedValueConstraint(i, kinit[i], self.model.val.nterms)()
                )
            elif icname.startswith('dihed'):
                constraints.append(
                    LowerLimitConstraint(i,   0*kjmol, self.model.val.nterms)()
                )
                constraints.append(
                    UpperLimitConstraint(i, 200*kjmol, self.model.val.nterms)()
                )
            else:
                constraints.append(
                    LowerLimitConstraint(i, 0.0, self.model.val.nterms)()
                )
        return tuple(constraints)

    def fun(self, k, do_grad=False):
        'Calculate the actual cost'
        chi2 = 0.5*np.dot(k.T, np.dot(self._A, k)) \
             - np.dot(self._B.T, k) + 0.5*self._C
        if do_grad:
            gchi2 = np.dot(self._A, k) - self._B
            return chi2, gchi2
        else:
            return chi2

    def estimate(self, fixed=None):
        '''
            Estimate the force constants by minimizing the cost function

            **Arguments*

            fixed
                A list containing bonds, angles, dihedrals and/or opdists
                if these ics should be kept fixed.
        '''
        kinit = self.model.val.get_fcs()
        constraints = self._define_constraints(fixed, kinit)
        self._update_lstsq_matrices()
        result = minimize(
            self.fun, kinit, method='SLSQP', constraints=constraints,
            tol=1e-9, options={'disp': False}
        )
        return result.x


class BaseConstraint(object):
    def __init__(self, ctype, npars):
        '''
            A class for defining constraints in the minimalization of the cost.

            **Arguments**

            ctype
                the type of the constraint, can be 'eq' (equality, zero) or
                'ineq' (inequality, non-negative)

            npars
                the number of parameters of the cost function
        '''
        self.ctype = ctype
        self.npars = npars

    def __call__(self):
        'return the constraint in scipy.optimize.minimize format'
        return {
            'type': self.ctype,
            'fun' : self._fun(),
            'jac' : self._jac()
        }

    def _fun(self):
        '''
            Method to return the function defining the constraint. The arguments
            of the returned function should be the force constants.
        '''
        raise NotImplementedError

    def _jac(self):
        '''
            Method to return the jacobian of the function. The argument
            of the returned function should be the force constants.
        '''
        raise NotImplementedError


class FixedValueConstraint(BaseConstraint):
    def __init__(self, index, value, npars):
        '''
            A fixed value constraint

            **Arguments**

            index
                the index of the constrained fc

            value
                the fixed value of the constrained fc

            npars
                the number of parameters of the cost function
        '''
        self.index = index
        self.value = value
        BaseConstraint.__init__(self, 'eq', npars)

    def _fun(self):
        '''
            Method to return the function defining the constraint. The arguments
            of the returned function should be the force constants.
        '''
        return lambda k: k[self.index] - self.value

    def _jac(self):
        '''
            Method to return the jacobian of the function. The argument
            of the returned function should be the force constants.
        '''
        jac = np.zeros(self.npars, float)
        jac[self.index] = 1.0
        return lambda k: jac


class LowerLimitConstraint(BaseConstraint):
    def __init__(self, index, lower, npars):
        '''
            An upper limit constraint

            **Arguments**

            index
                the index of the constrained fc

            lower
                the upper limit of the constrained fc

            npars
                the number of parameters of the cost function
        '''
        self.index = index
        self.lower = lower
        BaseConstraint.__init__(self, 'ineq', npars)

    def _fun(self):
        '''
            Method to return the function defining the constraint. The arguments
            of the returned function should be the force constants.
        '''
        return lambda k: k[self.index] - self.lower

    def _jac(self):
        '''
            Method to return the jacobian of the function. The argument
            of the returned function should be the force constants.
        '''
        jac = np.zeros(self.npars, float)
        jac[self.index] = 1.0
        return lambda k: jac


class UpperLimitConstraint(BaseConstraint):
    def __init__(self, index, upper, npars):
        '''
            An upper limit constraint

            **Arguments**

            index
                the index of the constrained fc

            upper
                the upper limit of the constrained fc

            npars
                the number of parameters of the cost function
        '''
        self.index = index
        self.upper = upper
        BaseConstraint.__init__(self, 'ineq', npars)

    def _fun(self):
        '''
            Method to return the function defining the constraint. The arguments
            of the returned function should be the force constants.
        '''
        return lambda k: self.upper - k[self.index]

    def _jac(self):
        '''
            Method to return the jacobian of the function. The argument
            of the returned function should be the force constants.
        '''
        jac = np.zeros(self.npars, float)
        jac[self.index] = -1.0
        return lambda k: jac