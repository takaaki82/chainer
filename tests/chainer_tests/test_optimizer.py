import copy
import unittest
import warnings

import mock
import numpy as np

import chainer
from chainer.backends import cuda
from chainer import optimizer
from chainer import optimizers
from chainer import testing
from chainer.testing import attr


class TestHyperparameter(unittest.TestCase):

    def setUp(self):
        self.parent = optimizer.Hyperparameter()
        self.parent.x = 1
        self.parent.y = 2
        self.child = optimizer.Hyperparameter(self.parent)
        self.child.y = 3
        self.child.z = 4

    def test_getattr(self):
        self.assertTrue(hasattr(self.parent, 'x'))
        self.assertEqual(self.parent.x, 1)
        self.assertTrue(hasattr(self.parent, 'y'))
        self.assertEqual(self.parent.y, 2)
        self.assertFalse(hasattr(self.parent, 'z'))

        self.assertTrue(hasattr(self.child, 'x'))
        self.assertEqual(self.child.x, 1)
        self.assertTrue(hasattr(self.child, 'y'))
        self.assertEqual(self.child.y, 3)
        self.assertTrue(hasattr(self.child, 'z'))
        self.assertEqual(self.child.z, 4)

    def test_get_dict(self):
        self.assertEqual(self.parent.get_dict(), {'x': 1, 'y': 2})
        self.assertEqual(self.child.get_dict(), {'x': 1, 'y': 3, 'z': 4})

    def test_repr(self):
        self.assertEqual(repr(self.parent), 'Hyperparameter(x=1, y=2)')
        self.assertEqual(repr(self.child), 'Hyperparameter(x=1, y=3, z=4)')

    def test_deep_copy(self):
        parent_copy, child_copy = copy.deepcopy([self.parent, self.child])
        self.assertEqual(self.child.get_dict(), child_copy.get_dict())
        self.assertEqual(self.parent.get_dict(), parent_copy.get_dict())
        self.assertIs(child_copy.parent, parent_copy)


class TestUpdateRule(unittest.TestCase):

    def setUp(self):
        self.data = np.ones((2, 3), np.float32)
        self.grad = np.ones_like(self.data)
        self.var = chainer.Variable(self.data, grad=self.grad)

        self.update_rule = optimizer.UpdateRule()
        self.update_rule.update_core_cpu = mock.MagicMock()
        self.update_rule.update_core_gpu = mock.MagicMock()

    def test_update_cpu(self):
        self.update_rule.update(self.var)
        self.assertEqual(self.update_rule.update_core_cpu.call_count, 1)
        self.assertEqual(self.update_rule.update_core_gpu.call_count, 0)

    @attr.gpu
    def test_update_gpu(self):
        self.var.to_gpu()
        self.update_rule.update(self.var)
        self.assertEqual(self.update_rule.update_core_cpu.call_count, 0)
        self.assertEqual(self.update_rule.update_core_gpu.call_count, 1)

    def check_add_hook(self, hook):
        self.update_rule.update(self.var)
        self.assertEqual(hook.call_count, 1)

        args = hook.call_args_list[0][0]
        self.assertEqual(len(args), 2)
        self.assertIs(args[0], self.update_rule)
        self.assertIs(args[1], self.var)

    def test_add_hook(self):
        hook = mock.MagicMock()
        self.update_rule.add_hook(hook)
        self.check_add_hook(hook)

    def test_add_hook_with_name(self):
        hook = mock.MagicMock()
        self.update_rule.add_hook(hook, name='hook')
        self.check_add_hook(hook)

    def test_remove_hook(self):
        hook = mock.MagicMock()
        self.update_rule.add_hook(hook, name='hook')
        self.update_rule.remove_hook('hook')
        self.update_rule.update(self.var)
        self.assertEqual(hook.call_count, 0)

    def test_add_hook_with_function_name(self):
        hook_body = mock.MagicMock()

        def foo(update_rule, data, grad):
            hook_body(update_rule, data, grad)

        self.update_rule.add_hook(foo)
        self.update_rule.remove_hook('foo')
        self.update_rule.update(self.var)
        self.assertEqual(hook_body.call_count, 0)

    def test_add_hook_no_name(self):
        class CallableWithoutName(object):
            def __call__(self, update_rule, param):
                pass

        with self.assertRaises(ValueError):
            self.update_rule.add_hook(CallableWithoutName())

    def test_add_hook_duplicated_name(self):
        self.update_rule.add_hook(mock.MagicMock(), name='foo')
        with self.assertRaises(ValueError):
            self.update_rule.add_hook(mock.MagicMock(), name='foo')

    def test_remove_hook_not_exist(self):
        with self.assertRaises(KeyError):
            self.update_rule.remove_hook('foo')

    def test_disabled_update_rule(self):
        self.update_rule.update_core = mock.MagicMock()
        self.update_rule.enabled = False
        self.update_rule.update(self.var)
        self.assertEqual(self.update_rule.update_core.call_count, 0)

        self.update_rule.enabled = True
        self.update_rule.update(self.var)
        self.assertEqual(self.update_rule.update_core.call_count, 1)

    def setup_state(self):
        def init_state(data):
            state = self.update_rule.state
            state['a'] = 0
            state['b'] = np.array([1, 2, 3], dtype=np.float32)
        self.update_rule.init_state = init_state

    @attr.gpu
    def test_state_copy_to_gpu(self):
        self.setup_state()

        def update_core(param):
            self.assertIsInstance(self.update_rule.state['a'], int)
            self.assertIsInstance(self.update_rule.state['b'], cuda.ndarray)

        self.update_rule.update_core = update_core
        self.var.to_gpu()
        self.update_rule.update(self.var)

    @attr.multi_gpu(2)
    def test_state_copy_to_another_gpu(self):
        self.setup_state()

        def update_core(param):
            self.assertIsInstance(self.update_rule.state['b'], cuda.ndarray)
            self.assertEqual(self.update_rule.state['b'].device.id, 1)

        # call update with arrays on GPU 0 (tested by another method)
        self.update_rule.update_core = lambda param: None
        self.update_rule.update(chainer.Variable(
            cuda.to_gpu(self.data, 0), grad=cuda.to_gpu(self.grad, 0)))
        # check if it copies the states correctly when arrays on another GPU
        # are passed
        self.update_rule.update_core = update_core
        self.update_rule.update(chainer.Variable(
            cuda.to_gpu(self.data, 1), grad=cuda.to_gpu(self.grad, 1)))

    @attr.gpu
    def test_state_copy_to_cpu(self):
        self.setup_state()

        def update_core(param):
            self.assertIsInstance(self.update_rule.state['a'], int)
            self.assertIsInstance(self.update_rule.state['b'], np.ndarray)

        self.var.to_gpu()
        self.update_rule.update(self.var)
        self.var.to_cpu()
        self.update_rule.update_core = update_core
        self.update_rule.update(self.var)


class TestOptimizerHook(unittest.TestCase):

    def setUp(self):
        self.optimizer = optimizer.Optimizer()
        self.target = SimpleLink(
            np.arange(6, dtype=np.float32).reshape(2, 3),
            np.arange(3, -3, -1, dtype=np.float32).reshape(2, 3))

    def test_add_hook(self):
        h1 = mock.MagicMock(timing='pre')
        h1.call_for_each_param = False
        self.optimizer.setup(self.target)
        self.optimizer.add_hook(h1, 'h1')
        self.optimizer.call_hooks()
        h1.assert_called_with(self.optimizer)

    def test_add_hook_call_for_each_param(self):
        h1 = mock.MagicMock(timing='pre')
        h1.call_for_each_param = True
        self.optimizer.setup(self.target)
        self.optimizer.add_hook(h1, 'h1')
        self.optimizer.call_hooks()
        h1.assert_called_with(self.target.param.update_rule, self.target.param)

    def test_remove_hook(self):
        h1 = mock.MagicMock(timing='pre')
        self.optimizer.setup(self.target)
        self.optimizer.add_hook(h1, 'h1')
        self.optimizer.remove_hook('h1')
        self.optimizer.call_hooks()
        self.assertFalse(h1.called)

    def test_duplicated_hook(self):
        self.optimizer.setup(self.target)
        self.optimizer.add_hook(lambda s: None, 'h1', timing='pre')
        with self.assertRaises(KeyError):
            self.optimizer.add_hook(lambda s: None, 'h1', timing='pre')

    def test_invalid_hook(self):
        with self.assertRaises(TypeError):
            self.optimizer.add_hook(1)

    def test_add_hook_before_setup(self):
        with self.assertRaises(RuntimeError):
            self.optimizer.add_hook(lambda s: None, 'h1')


class SimpleLink(chainer.Link):

    def __init__(self, w, g):
        super(SimpleLink, self).__init__()
        with self.init_scope():
            self.param = chainer.Parameter(w)
            self.param.grad = g


class TestGradientMethod(unittest.TestCase):

    def setUp(self):
        self.optimizer = chainer.GradientMethod()
        self.target = chainer.ChainList(
            SimpleLink(np.arange(3).astype(np.float32),
                       np.arange(3).astype(np.float32)),
            SimpleLink(np.arange(3).astype(np.float32),
                       np.arange(3).astype(np.float32)))
        self.optimizer.create_update_rule = mock.MagicMock

    def setup_cpu(self):
        self.optimizer.setup(self.target)

    def setup_gpu(self, device=None):
        self.target.to_gpu(device)
        self.optimizer.setup(self.target)

    def test_setup(self):
        create_update_rule = mock.MagicMock()
        self.optimizer.create_update_rule = create_update_rule
        self.optimizer.setup(self.target)

        self.assertEqual(create_update_rule.call_count, 2)
        self.assertEqual(create_update_rule.call_args_list[0], [(), {}])
        self.assertEqual(create_update_rule.call_args_list[1], [(), {}])

    def check_update(self):
        self.assertEqual(self.optimizer.t, 0)

        self.optimizer.update()
        self.assertEqual(self.optimizer.t, 1)

        self.target[0].param.update_rule.update.assert_called_once_with(
            self.target[0].param)
        self.target[1].param.update_rule.update.assert_called_once_with(
            self.target[1].param)

    def test_update_cpu(self):
        self.setup_cpu()
        self.check_update()

    @attr.gpu
    def test_update_gpu(self):
        self.setup_gpu()
        self.check_update()


@testing.parameterize(*testing.product({
    'shape': [(4, 3, 2)],
    'dtype': [np.float16, np.float32, np.float64],
    'loss_scale': [None, 1, 10],
}))
class TestGradientMethodLossScale(unittest.TestCase):

    def setUp(self):
        param0_data = np.random.uniform(-1, 1, self.shape).astype(self.dtype)
        param0_grad = np.copy(param0_data)
        param1_data = np.random.uniform(-1, 1, self.shape).astype(self.dtype)
        param1_grad = np.copy(param1_data)
        self.target = chainer.ChainList(
            SimpleLink(param0_data, param0_grad),
            SimpleLink(param1_data, param1_grad))
        lr = 1.0
        if self.loss_scale is not None:
            lr = self.loss_scale
            for i in range(2):
                self.target[i].param._loss_scale = self.loss_scale
        self.optimizer = chainer.optimizers.SGD(lr)

    def setup_cpu(self):
        self.optimizer.setup(self.target)

    def setup_gpu(self, device=None):
        self.target.to_gpu(device)
        self.optimizer.setup(self.target)

    def check_update(self):
        self.optimizer.update()
        xp = cuda.get_array_module(self.target[0].param)
        expected_data = xp.zeros(self.shape, dtype=self.dtype)
        rtol, atol = 1e-4, 1e-5
        if self.dtype is np.float16:
            rtol, atol = 1e-1, 1e-2
        for i in range(2):
            testing.assert_allclose(self.target[i].param.data, expected_data,
                                    rtol=rtol, atol=atol)

    def test_update_cpu(self):
        self.setup_cpu()
        self.check_update()

    @attr.gpu
    def test_update_gpu(self):
        self.setup_gpu()
        self.check_update()


class TestCleargradHook(unittest.TestCase):

    def setUp(self):
        self.target = SimpleLink(
            np.arange(6, dtype=np.float32).reshape(2, 3),
            np.arange(3, -3, -1, dtype=np.float32).reshape(2, 3))

    def check_cleargrad(self):
        opt = optimizers.SGD(lr=1)
        opt.setup(self.target)
        opt.add_hook(CleargradHook(self))
        opt.add_hook(DummyHook(self))

        opt.update()

    def test_cleargrad_cpu(self):
        self.check_cleargrad()

    @attr.gpu
    def test_cleargrad_gpu(self):
        self.target.to_gpu()
        self.check_cleargrad()


class DummyOptimizer(chainer.GradientMethod):

    def __init__(self, test):
        super(DummyOptimizer, self).__init__()
        self.test = test

    def create_update_rule(self):
        return mock.MagicMock()


class DummyHook(object):

    name = 'Dummy'
    timing = 'pre'

    def __init__(self, test):
        self.test = test

    def __call__(self, opt):
        for param in opt.target.params():
            # Confirm all grads are not None
            self.test.assertIsNotNone(param.grad)


class CleargradHook(object):

    name = 'Cleargrad'
    timing = 'pre'

    def __init__(self, _):
        pass

    def __call__(self, opt):
        for param in opt.target.params():
            # Clear all grads
            param.cleargrad()


class TestGradientMethodClearGrads(unittest.TestCase):

    def setUp(self):
        self.optimizer = DummyOptimizer(self)
        self.target = SimpleLink(
            np.arange(3).astype(np.float32),
            np.arange(3).astype(np.float32))
        self.optimizer.setup(self.target)
        self.optimizer.add_hook(DummyHook(self))

    def test_update(self):
        self.target.cleargrads()
        self.optimizer.update()


class TestDeprecatedOptimizerHooksEmitsWarning(unittest.TestCase):

    def setUp(self):
        self.context = warnings.catch_warnings(record=True)
        self.warnings = self.context.__enter__()
        warnings.filterwarnings(action='always', category=DeprecationWarning)

    def tearDown(self):
        self.context.__exit__()

    def test_gradient_clipping(self):
        chainer.optimizer.GradientClipping(1.)
        self.assertEqual(len(self.warnings), 1)
        self.assertIs(self.warnings[-1].category, DeprecationWarning)

    def test_gradient_hard_clipping(self):
        chainer.optimizer.GradientHardClipping(1., 2.)
        self.assertEqual(len(self.warnings), 1)
        self.assertIs(self.warnings[-1].category, DeprecationWarning)

    def test_gradient_noise(self):
        chainer.optimizer.GradientNoise(1.)
        self.assertEqual(len(self.warnings), 1)
        self.assertIs(self.warnings[-1].category, DeprecationWarning)

    def test_lasso(self):
        chainer.optimizer.Lasso(1.)
        self.assertEqual(len(self.warnings), 1)
        self.assertIs(self.warnings[-1].category, DeprecationWarning)

    def test_weight_decay(self):
        chainer.optimizer.WeightDecay(1.)
        self.assertEqual(len(self.warnings), 1)
        self.assertIs(self.warnings[-1].category, DeprecationWarning)

    def test_gradient_lars(self):
        chainer.optimizer.GradientLARS(1e-2, 0.2, 1e-9)
        self.assertEqual(len(self.warnings), 1)
        self.assertIs(self.warnings[-1].category, DeprecationWarning)


testing.run_module(__name__, __file__)
